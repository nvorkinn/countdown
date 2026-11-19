"""The TfL provider's mapping from TfL's stops and arrivals to the stored StopDepartures, with TflSource mocked."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import fakeredis

from countdown_providers.tfl.main import TflProvider, TimetableReader, to_stop_departures
from countdown_shared.store import keys
from countdown_shared.store.models import Departure, StopDepartures
from countdown_shared.store.timetable import StoredTimetable, TimetabledDeparture
from countdown_shared.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_shared.tfl.tfl_client import TflSource

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

BUS_STOP = SingleStopPoint.model_validate(
    {
        "naptanId": "490000123W",
        "commonName": "Elephant & Castle",
        "modes": ["bus"],
        "additionalProperties": [],
        "stopType": "NaptanPublicBusCoachTram",
        "stopLetter": "W",
    }
)
TUBE_STATION = MetroStopPoint.model_validate(
    {
        "naptanId": "940GZZLUKNG",
        "commonName": "Kennington Underground Station",
        "modes": ["tube"],
        "additionalProperties": [],
        "stopType": "NaptanMetroStation",
    }
)


def bus(line, destination, seconds, vehicle_id=None):
    return BusArrival.model_validate(
        {
            "naptanId": BUS_STOP.naptan_id,
            "lineName": line,
            "timeToStation": seconds,
            "modeName": "bus",
            "destinationName": destination,
            "vehicleId": vehicle_id,
        }
    )


def train(towards, seconds, destination_naptan_id=None, vehicle_id=None, platform_name=None):
    return TubeArrival.model_validate(
        {
            "naptanId": TUBE_STATION.naptan_id,
            "lineName": "Northern",
            "timeToStation": seconds,
            "modeName": "tube",
            "towards": towards,
            "destinationNaptanId": destination_naptan_id,
            "vehicleId": vehicle_id,
            "platformName": platform_name,
        }
    )


def make_provider(stops):
    """Make a provider, and its mocked source, which resolves the NaPTAN ids in `stops` (id -> stop)."""
    source = MagicMock(spec=TflSource)
    source.resolve.side_effect = stops.get
    return TflProvider(source, now=lambda: NOW), source


def test_bus_stop_maps_to_stop_departures_soonest_first():
    stored = to_stop_departures(
        BUS_STOP,
        [bus("12", "Oxford Circus, Regent Street", 300, "LX11AVC"), bus("171", "Elephant & Castle", 60, "LTZ1234")],
        NOW,
    )
    assert stored == StopDepartures(
        fetched_at=NOW,
        stop_id="490000123W",
        name="Elephant & Castle",
        mode="bus",
        stop_letter="W",
        departures=[
            Departure(
                line="171",
                destination="Elephant & Castle",
                expected_at=NOW + timedelta(seconds=60),
                vehicle_id="LTZ1234",
            ),
            # TfL's model has already trimmed the destination at the comma. A bus has no platform.
            Departure(
                line="12",
                destination="Oxford Circus",
                expected_at=NOW + timedelta(seconds=300),
                vehicle_id="LX11AVC",
            ),
        ],
    )


def test_tube_station_uses_towards_and_drops_trains_terminating_there():
    stored = to_stop_departures(
        TUBE_STATION,
        [train("Morden", 120), train("Kennington", 30, destination_naptan_id=TUBE_STATION.naptan_id)],
        NOW,
    )
    assert stored.mode == "tube"
    assert stored.stop_letter is None
    assert [(d.destination, d.time_to_station(NOW)) for d in stored.departures] == [("Morden", 120)]


def test_a_train_keeps_its_platform_and_vehicle_and_a_missing_id_is_none():
    stored = to_stop_departures(
        TUBE_STATION,
        [
            train("Morden", 120, vehicle_id="031", platform_name="Southbound - Platform 2"),
            train("Edgware via Bank", 240, vehicle_id=""),
        ],
        NOW,
    )
    assert [(d.vehicle_id, d.platform) for d in stored.departures] == [("031", "Southbound - Platform 2"), (None, None)]


def test_stop_with_nothing_due_still_maps():
    assert to_stop_departures(BUS_STOP, [], NOW).departures == []


def test_time_to_station_counts_down_from_now_and_stops_at_zero():
    departure = Departure(line="12", destination="X", expected_at=NOW + timedelta(seconds=90))
    assert departure.time_to_station(NOW + timedelta(seconds=30)) == 60
    assert departure.time_to_station(NOW + timedelta(seconds=120)) == 0


def test_fetch_returns_a_model_per_stop():
    provider, source = make_provider({BUS_STOP.naptan_id: BUS_STOP, TUBE_STATION.naptan_id: TUBE_STATION})
    source.arrivals.side_effect = lambda stop: [bus("12", "Peckham", 60)] if stop is BUS_STOP else [train("Morden", 30)]

    results = asyncio.run(provider.fetch([BUS_STOP.naptan_id, TUBE_STATION.naptan_id]))

    assert set(results) == {BUS_STOP.naptan_id, TUBE_STATION.naptan_id}
    assert results[BUS_STOP.naptan_id].departures[0].line == "12"
    assert results[TUBE_STATION.naptan_id].departures[0].destination == "Morden"
    assert provider.output_key(BUS_STOP.naptan_id) == keys.departures(BUS_STOP.naptan_id)


def test_fetch_leaves_out_unknown_and_failing_stops_without_raising(caplog):
    provider, source = make_provider({BUS_STOP.naptan_id: BUS_STOP, TUBE_STATION.naptan_id: TUBE_STATION})

    def arrivals(stop):
        if stop is TUBE_STATION:
            msg = "TfL said 500"
            raise RuntimeError(msg)
        return []

    source.arrivals.side_effect = arrivals

    results = asyncio.run(provider.fetch(["not-a-stop", TUBE_STATION.naptan_id, BUS_STOP.naptan_id]))

    assert list(results) == [BUS_STOP.naptan_id]
    assert "isn't a bus stop or Tube station" in caplog.text
    # By its reason, not its text: a failed request's quotes the URL, TfL's app key and all.
    assert f"Couldn't fetch TfL stop {TUBE_STATION.naptan_id}: RuntimeError" in caplog.messages
    assert "TfL said 500" not in caplog.text


def test_fetch_resolves_each_stop_only_once():
    provider, source = make_provider({BUS_STOP.naptan_id: BUS_STOP})
    source.arrivals.return_value = []
    asyncio.run(provider.fetch([BUS_STOP.naptan_id]))
    asyncio.run(provider.fetch([BUS_STOP.naptan_id]))
    assert source.resolve.call_count == 1
    assert source.arrivals.call_count == 2


def test_an_unresolvable_stop_is_asked_again_next_time():
    # It may only have failed for now (or been added to TfL since).
    provider, source = make_provider({})
    asyncio.run(provider.fetch(["not-a-stop"]))
    asyncio.run(provider.fetch(["not-a-stop"]))
    assert source.resolve.call_count == 2


def timed(line, destination, minutes):
    return TimetabledDeparture(
        line=line, line_id=line.lower(), destination=destination, departs_at=NOW + timedelta(minutes=minutes)
    )


def timetable(*departures):
    return StoredTimetable(fetched_at=NOW, stop_id=TUBE_STATION.naptan_id, departures=list(departures))


def test_a_line_tfl_predicts_nothing_for_gets_its_timetabled_departures_marked_scheduled():
    stored = to_stop_departures(
        TUBE_STATION,
        [],
        NOW,
        timetable(
            timed("Northern", "Morden", -5), timed("Northern", "Morden", 102), timed("Northern", "High Barnet", 98)
        ),
    )
    # Not one that has gone already.
    assert [(d.destination, d.expected_at, d.scheduled) for d in stored.departures] == [
        ("High Barnet", NOW + timedelta(minutes=98), True),
        ("Morden", NOW + timedelta(minutes=102), True),
    ]


def test_a_line_tfl_predicts_keeps_only_its_predictions_and_the_others_are_merged_soonest_first():
    stored = to_stop_departures(
        TUBE_STATION,
        [train("Morden", 120)],
        NOW,
        timetable(timed("Northern", "Morden", 1), timed("Victoria", "Brixton", 1), timed("Victoria", "Brixton", 30)),
    )
    assert [(d.line, d.time_to_station(NOW), d.scheduled) for d in stored.departures] == [
        ("Victoria", 60, True),
        ("Northern", 120, False),
        ("Victoria", 1800, True),
    ]


def test_fetch_reads_the_stops_timetables_from_the_store():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    source = MagicMock(spec=TflSource)
    source.resolve.side_effect = {TUBE_STATION.naptan_id: TUBE_STATION, BUS_STOP.naptan_id: BUS_STOP}.get
    source.arrivals.return_value = []
    provider = TflProvider(source, TimetableReader(redis), now=lambda: NOW)

    async def cycle():
        await redis.set(
            keys.timetable(TUBE_STATION.naptan_id), timetable(timed("Northern", "Morden", 90)).model_dump_json()
        )
        await redis.set(keys.timetable(BUS_STOP.naptan_id), "not a timetable")
        return await provider.fetch([TUBE_STATION.naptan_id, BUS_STOP.naptan_id])

    results = asyncio.run(cycle())

    assert [d.scheduled for d in results[TUBE_STATION.naptan_id].departures] == [True]
    # An unreadable timetable leaves just the predictions.
    assert results[BUS_STOP.naptan_id].departures == []
