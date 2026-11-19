"""The TfL timetable provider: picking the day's schedule, working out the next departures, and the provider's cache."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import fakeredis
import pytest

from countdown_providers.tfl_timetable.main import BankHolidayReader, TflTimetableProvider
from countdown_providers.tfl_timetable.schedule import days_of, next_departures, schedule_for, short_name
from countdown_shared.store import keys
from countdown_shared.store.timetable import StoredTimetable
from countdown_shared.tfl.models import Line, MetroStopPoint, Schedule, TimetableResponse
from countdown_shared.tfl.tfl_client import TflSource

KENNINGTON = "940GZZLUKNG"
# Thursday 19 November 2026, 04:00 in London (GMT).
NOW = datetime(2026, 11, 19, 4, 0, tzinfo=UTC)

STATION = MetroStopPoint.model_validate(
    {
        "naptanId": KENNINGTON,
        "commonName": "Kennington Underground Station",
        "modes": ["tube"],
        "stopType": "NaptanMetroStation",
        "lines": [{"id": "northern", "name": "Northern"}],
    }
)


def journey(hour, minute, interval_id=0):
    return {"hour": str(hour), "minute": str(minute), "intervalId": interval_id}


def answer(schedules, stop_id=KENNINGTON):
    """TfL's answer for the Northern line southbound from Kennington: interval 0 ends at Morden, 1 back here."""
    return TimetableResponse.model_validate(
        {
            "lineId": "northern",
            "lineName": "Northern",
            "direction": "inbound",
            "stops": [{"id": "940GZZLUMDN", "name": "Morden Underground Station"}, {"id": KENNINGTON, "name": "K"}],
            "timetable": {
                "departureStopId": stop_id,
                "routes": [
                    {
                        "stationIntervals": [
                            {"id": "0", "intervals": [{"stopId": "940GZZLUOVL"}, {"stopId": "940GZZLUMDN"}]},
                            {"id": "1", "intervals": [{"stopId": KENNINGTON}]},
                        ],
                        "schedules": [
                            {"name": name, "knownJourneys": journeys} for name, journeys in schedules.items()
                        ],
                    }
                ],
            },
        }
    )


WEEK = {
    "Monday - Thursday": [journey(5, 42), journey(5, 54), journey(6, 1), journey(24, 46)],
    "Friday": [journey(5, 42), journey(27, 30)],
    "Saturday (also Good Friday)": [journey(3, 37), journey(6, 6)],
    "Sunday": [journey(7, 38)],
}


@pytest.mark.parametrize(
    ("name", "days"),
    [
        ("Monday - Thursday", {0, 1, 2, 3}),
        ("Monday to Friday", {0, 1, 2, 3, 4}),
        ("Friday", {4}),
        ("Saturday (also Good Friday)", {5}),
        ("Sunday", {6}),
        ("Saturday - Sunday", {5, 6}),
        ("School holidays", set()),
    ],
)
def test_a_schedules_name_says_its_days(name, days):
    assert days_of(name) == days


def test_a_bank_holiday_runs_sundays_good_friday_saturdays_and_christmas_none():
    schedules = [Schedule.model_validate({"name": name, "knownJourneys": []}) for name in WEEK]
    names = {
        day: (schedule.name if (schedule := schedule_for(schedules, day, holidays)) else None)
        for day, holidays in [
            (date(2026, 11, 19), {}),
            (date(2026, 8, 31), {date(2026, 8, 31): "Summer bank holiday"}),
            (date(2027, 3, 26), {date(2027, 3, 26): "Good Friday"}),
            (date(2026, 12, 25), {date(2026, 12, 25): "Christmas Day"}),
        ]
    }
    assert list(names.values()) == ["Monday - Thursday", "Sunday", "Saturday (also Good Friday)", None]


def test_the_next_departures_are_from_now_on_with_last_nights_after_midnight_and_none_ending_here():
    departures = next_departures(
        answer(WEEK | {"Monday - Thursday": [*WEEK["Monday - Thursday"], journey(5, 0, 1)]}), KENNINGTON, NOW, {}
    )
    # Wednesday's 24:46 is 00:46 today, gone by 04:00; the 05:00 (interval 1) ends here.
    assert [(d.departs_at, d.destination, d.line) for d in departures] == [
        (datetime(2026, 11, 19, 5, 42, tzinfo=UTC), "Morden", "Northern"),
        (datetime(2026, 11, 19, 5, 54, tzinfo=UTC), "Morden", "Northern"),
        (datetime(2026, 11, 19, 6, 1, tzinfo=UTC), "Morden", "Northern"),
        (datetime(2026, 11, 20, 0, 46, tzinfo=UTC), "Morden", "Northern"),
    ]


def test_a_friday_nights_trains_run_into_saturday_morning():
    saturday_2am = datetime(2026, 11, 21, 2, 0, tzinfo=UTC)
    departures = next_departures(answer(WEEK), KENNINGTON, saturday_2am, {})
    assert [d.departs_at for d in departures][:2] == [
        datetime(2026, 11, 21, 3, 30, tzinfo=UTC),
        datetime(2026, 11, 21, 3, 37, tzinfo=UTC),
    ]


def test_only_the_first_few_per_destination_are_kept():
    many = {"Monday - Thursday": [journey(6, minute) for minute in range(0, 60, 5)]}
    assert len(next_departures(answer(many), KENNINGTON, NOW, {})) == 4


def test_another_stops_timetable_gives_nothing():
    # TfL answers a direction the line doesn't run from a stop with a neighbour's timetable.
    assert next_departures(answer(WEEK, stop_id="940GZZLUOVL"), KENNINGTON, NOW, {}) == []


def test_times_are_london_time_in_summer():
    july = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)  # 04:00 BST
    departures = next_departures(answer(WEEK), KENNINGTON, july, {})
    assert departures[0].departs_at == datetime(2026, 7, 16, 4, 42, tzinfo=UTC)


def test_a_stations_kind_is_left_off_its_name():
    assert short_name("Morden Underground Station") == "Morden"
    assert short_name("Elephant & Castle") == "Elephant & Castle"


def make_provider(redis=None, statuses=()):
    source = MagicMock(spec=TflSource)
    source.resolve.side_effect = {KENNINGTON: STATION}.get
    source.timetable.side_effect = lambda line_id, stop_id, direction: (
        answer(WEEK) if direction == "inbound" else TimetableResponse.model_validate({})
    )
    source.line_status_between.return_value = [
        Line.model_validate(
            {
                "id": "northern",
                "name": "Northern",
                "lineStatuses": [{"statusSeverity": 0, "statusSeverityDescription": s}],
            }
        )
        for s in statuses
    ]
    redis = redis or fakeredis.FakeAsyncRedis(decode_responses=True)
    clock = {"now": NOW}
    provider = TflTimetableProvider(source, BankHolidayReader(redis), now=lambda: clock["now"])
    return provider, source, clock


def test_fetch_stores_the_stops_next_departures_and_fetches_each_timetable_once_a_day():
    provider, source, clock = make_provider(statuses=["Service Closed"])

    first = asyncio.run(provider.fetch([KENNINGTON]))[KENNINGTON]
    clock["now"] = NOW + timedelta(minutes=15)
    asyncio.run(provider.fetch([KENNINGTON]))
    assert source.timetable.call_count == 2  # inbound and outbound, once
    clock["now"] = NOW + timedelta(days=1)
    asyncio.run(provider.fetch([KENNINGTON]))
    assert source.timetable.call_count == 4

    assert isinstance(first, StoredTimetable)
    assert first.departures[0].departs_at == datetime(2026, 11, 19, 5, 42, tzinfo=UTC)
    assert provider.output_key(KENNINGTON) == keys.timetable(KENNINGTON)


def test_a_line_with_a_planned_closure_has_no_timetabled_departures():
    provider, _, _ = make_provider(statuses=["Planned Closure"])
    assert asyncio.run(provider.fetch([KENNINGTON]))[KENNINGTON].departures == []


def test_a_failed_timetable_fetch_keeps_yesterdays_copy(caplog):
    provider, source, clock = make_provider()
    asyncio.run(provider.fetch([KENNINGTON]))
    source.timetable.side_effect = RuntimeError("TfL said 500 for app_key=secret")
    clock["now"] = NOW + timedelta(days=1)

    stored = asyncio.run(provider.fetch([KENNINGTON]))[KENNINGTON]

    assert stored.departures
    assert "secret" not in caplog.text


def test_bank_holidays_come_from_the_store():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    value = {
        "fetched_at": "2026-11-18T06:00:00Z",
        "divisions": {"england-and-wales": {"events": [{"title": "Christmas Day", "date": "2026-12-25"}]}},
    }

    async def read():
        before = await BankHolidayReader(redis).holidays()
        await redis.set(keys.BANK_HOLIDAYS, json.dumps(value))
        return before, await BankHolidayReader(redis).holidays()

    assert asyncio.run(read()) == ({}, {date(2026, 12, 25): "Christmas Day"})


def test_a_direction_the_line_doesnt_run_from_the_stop_is_quietly_none(caplog):
    provider, source, _ = make_provider()
    not_found = RuntimeError("404")
    not_found.response = MagicMock(status_code=404)

    def timetable(line_id, stop_id, direction):
        if direction == "outbound":
            raise not_found
        return answer(WEEK)

    source.timetable.side_effect = timetable

    assert asyncio.run(provider.fetch([KENNINGTON]))[KENNINGTON].departures
    assert "Couldn't fetch" not in caplog.text
