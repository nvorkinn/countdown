import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import requests
import responses
from config_factory import make_config
from PIL import Image, ImageDraw
from test_utils import REGISTRATION

from countdown_api.models import DepartureStop, Tfl
from countdown_core.core.api_registry import section_for
from countdown_core.departures.departures_client import DeparturesSettings
from countdown_core.notices import notice_board_client as board_module
from countdown_core.notices.calendar import (
    BANK_HOLIDAYS_URL,
    BankHolidaySource,
    ClockChangeSource,
)
from countdown_core.notices.floods import FLOODS_URL, FloodWarningsSource
from countdown_core.notices.location import POSTCODES_URL
from countdown_core.notices.met_office import MetOfficeWarningsSource, parse_warnings, region_for
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.notices.notice_board_panel import ROW_HEIGHT, NoticeBoardPanel
from countdown_core.notices.notice_styles import STYLES
from countdown_core.notices.tfl import TFL_URL, TflLineStatusSource, TflRoadSource, TflStationSource
from countdown_core.tfl.models import Postcode
from countdown_core.utils.clock import format_clock

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
SOUTHWARK = Postcode(
    postcode="SE17 3LL",
    latitude=51.49,
    longitude=-0.1,
    country="England",
    region="London",
    admin_district="Southwark",
)


@pytest.fixture
def api():
    with responses.RequestsMock() as rsps:
        yield rsps


# --- Met Office ------------------------------------------------------------------

AMBER_AND_YELLOW = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Met Office warnings for London &amp; South East England</title>
  <item>
    <title>Amber warning of wind affecting London &amp; South East England</title>
    <description>Amber warning of wind affecting London &amp; South East England: Southwark, Lambeth valid from 0600 Fri 25 Sep to 2100 Fri 25 Sep</description>
  </item>
  <item>
    <title>Yellow warning of rain affecting London &amp; South East England</title>
    <description>Yellow warning of rain affecting London &amp; South East England valid from 1800 Fri 25 Sep to 0900 Sat 26 Sep</description>
  </item>
  <item>
    <title>Something the Met Office has never said before</title>
    <description>No dates here</description>
  </item>
</channel></rss>"""  # noqa: E501 -- a real feed's lines, which can't be wrapped without changing the text


def test_met_office_reads_colour_hazard_and_end_time():
    notices = parse_warnings(AMBER_AND_YELLOW, NOW)

    assert [(n.severity, n.text) for n in notices] == [
        (Severity.WARNING, "Amber warning: wind until Fri"),
        (Severity.INFO, "Yellow warning: rain until Sat"),
        (Severity.WARNING, "Something the Met Office has never said before"),
    ]
    assert notices[0].valid_until == datetime(2026, 9, 25, 20, 0, tzinfo=UTC)  # 21:00 BST
    assert notices[0].display_time == notices[0].valid_until
    assert notices[0].display_time.hour == 21
    assert notices[2].valid_until is None
    assert notices[2].display_time is None


def test_met_office_warning_into_january_read_in_december_ends_next_year():
    feed = b"""<rss><channel><item><title>Red warning of snow affecting Grampian</title>
        <description>valid from 1800 Wed 31 Dec to 1200 Thu 01 Jan</description></item></channel></rss>"""

    [notice] = parse_warnings(feed, datetime(2025, 12, 31, 20, 0, tzinfo=UTC))

    assert notice.severity == Severity.SEVERE
    assert notice.valid_until == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_met_office_empty_feed_has_no_notices():
    assert parse_warnings(b"<rss><channel><title>Met Office warnings</title></channel></rss>", NOW) == []


@pytest.mark.parametrize(
    ("now", "hour_24", "hour_12"),
    [
        (datetime(2026, 10, 24, 18, tzinfo=UTC), "02:00", "2:00 AM"),
        (datetime(2027, 3, 27, 18, tzinfo=UTC), "01:00", "1:00 AM"),
    ],
)
@pytest.mark.parametrize("width", [262, 470, 790])
def test_cached_notice_clocks_reformat_without_rewriting_feed_text(now, hour_24, hour_12, width, monkeypatch):
    warnings = parse_warnings(AMBER_AND_YELLOW, NOW)
    reminders = ClockChangeSource(requests.Session()).fetch(now)
    free_text = "Works at 18:00"
    notices = [*warnings[:2], *reminders, Notice("TfL", Severity.INFO, free_text)]
    expiries = [n.valid_until for n in notices]
    panel = NoticeBoardPanel(notices)
    texts = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, fill=None, font=None, anchor=None, **kwargs):  # noqa: PLR0913, PLR0917 -- ImageDraw.text's own arguments
        if anchor == "lm":
            left, top, right, bottom = draw.textbbox(xy, text, font=font, anchor=anchor)
            assert 0 <= left < right <= width
            assert 0 <= top < bottom <= 200
            texts.append(text)
        return original(draw, xy, text, fill, font=font, anchor=anchor, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    first = panel.render(width, 200)
    if width >= 470:
        assert any(text.endswith("21:00") for text in texts)
        assert any(text.endswith("09:00") for text in texts)
        assert any(text.endswith(hour_24) for text in texts)
    legacy_notices = [
        replace(n, text=f"{n.text} {format_clock(n.display_time)}", display_time=None) if n.display_time else n
        for n in notices
    ]
    assert first.tobytes() == NoticeBoardPanel(legacy_notices).render(width, 200).tobytes()
    texts.clear()

    panel.render(width, 200, clock_format="12h")
    assert any(text.endswith("9:00 PM") for text in texts)
    assert any(text.endswith("9:00 AM") for text in texts)
    assert any(text.endswith(hour_12) for text in texts)
    assert free_text in texts
    assert [n.valid_until for n in notices] == expiries
    assert first.tobytes() == panel.render(width, 200).tobytes()


@pytest.mark.parametrize(
    ("hhmm", "expected"),
    [("0007", "12:07 AM"), ("1207", "12:07 PM")],
)
def test_warning_expiry_midnight_and_noon_are_formatted_when_drawn(hhmm, expected, monkeypatch):
    feed = f"""<rss><channel><item><title>Yellow warning of rain affecting London</title>
        <description>valid from 0000 Fri 25 Sep to {hhmm} Sat 26 Sep</description></item></channel></rss>""".encode()
    [notice] = parse_warnings(feed, NOW)
    texts = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    NoticeBoardPanel([notice]).render(790, 100, clock_format="12h")

    assert f"Yellow warning: rain until Sat {expected}" in texts


@pytest.mark.parametrize(
    ("country", "region", "district", "expected"),
    [
        ("England", "London", "Southwark", "se"),
        ("England", "Yorkshire and The Humber", "Leeds", "yh"),
        ("Wales", None, "Cardiff", "wl"),
        ("Northern Ireland", None, "Belfast", "ni"),
        ("Scotland", None, "Glasgow City", "st"),
        ("Scotland", None, "City of Edinburgh", "dg"),
        ("Scotland", None, "Somewhere New", "UK"),
    ],
)
def test_met_office_region_from_postcode(country, region, district, expected):
    location = SOUTHWARK.model_copy(update={"country": country, "region": region, "admin_district": district})
    assert region_for(location) == expected


def test_met_office_logs_the_area_not_the_postcode_when_it_has_no_region(caplog):
    location = SOUTHWARK.model_copy(update={"country": "Scotland", "region": None, "admin_district": "Somewhere New"})

    with caplog.at_level("WARNING", logger="countdown_core.notices.met_office"):
        region_for(location)

    assert caplog.messages == ["No Met Office warning region for Somewhere New; using the whole UK"]


def test_met_office_source_fetches_its_region(api):
    api.add(
        responses.GET, "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/se", body=AMBER_AND_YELLOW
    )

    notices = MetOfficeWarningsSource(requests.Session(), SOUTHWARK).fetch(NOW)

    assert len(notices) == 3


# --- Floods ----------------------------------------------------------------------


def test_floods_maps_severity_levels_and_skips_ones_no_longer_in_force(api):
    items = [
        {"description": "River Thames at Southwark", "severity": "Flood warning", "severityLevel": 2},
        {"description": "Tidal Thames", "severity": "Severe Flood Warning", "severityLevel": 1},
        {"description": "Lower River Effra", "severity": "Flood alert", "severityLevel": 3},
        {"description": "Old news", "severity": "Warning no Longer in Force", "severityLevel": 4},
    ]
    api.add(responses.GET, FLOODS_URL, json={"items": items})

    notices = FloodWarningsSource(requests.Session(), SOUTHWARK).fetch(NOW)

    assert [(n.severity, n.text) for n in notices] == [
        (Severity.WARNING, "Flood warning: River Thames at Southwark"),
        (Severity.SEVERE, "Severe flood warning: Tidal Thames"),
        (Severity.INFO, "Flood alert: Lower River Effra"),
    ]
    assert api.calls[0].request.params == {"lat": "51.49", "long": "-0.1", "dist": "5"}


# --- TfL -------------------------------------------------------------------------

TFL = Tfl(app_key="key", stop_ids=["940GZZLUKNG", "490000077E"])


def _stop(naptan_id: str, stop_type: str, lines: list[str], **extra) -> dict:
    return {
        "naptanId": naptan_id,
        "commonName": naptan_id,
        "modes": [],
        "additionalProperties": [],
        "stopType": stop_type,
        "lines": [{"id": line, "name": line.title()} for line in lines],
        **extra,
    }


def _status(
    severity: int, description: str, periods: list[tuple[datetime, datetime]] = (), reason: str | None = None
) -> dict:
    return {
        "statusSeverity": severity,
        "statusSeverityDescription": description,
        "reason": reason,
        "validityPeriods": [{"fromDate": f.isoformat(), "toDate": t.isoformat()} for f, t in periods],
    }


def test_line_status_reports_current_problems_on_the_stops_lines(api):
    api.add(
        responses.GET, f"{TFL_URL}/StopPoint/940GZZLUKNG", json=_stop("940GZZLUKNG", "NaptanMetroStation", ["northern"])
    )
    bus_stop = _stop("490000077E", "NaptanPublicBusCoachTram", ["12", "northern"], stopLetter="E")
    api.add(responses.GET, f"{TFL_URL}/StopPoint/490000077E", json=bus_stop)
    today = (NOW - timedelta(hours=2), NOW + timedelta(hours=10))
    next_week = (NOW + timedelta(days=7), NOW + timedelta(days=8))
    lines = [
        {
            "id": "northern",
            "name": "Northern",
            "modeName": "tube",
            "lineStatuses": [_status(6, "Severe Delays", [today]), _status(3, "Part Suspended", [next_week])],
        },
        {"id": "12", "name": "12", "modeName": "bus", "lineStatuses": [_status(10, "Good Service")]},
    ]
    api.add(responses.GET, f"{TFL_URL}/Line/northern,12/Status", json=lines)
    source = TflLineStatusSource(requests.Session(), TFL)

    notices = source.fetch(NOW)

    # No reason given: the status itself, tagged with the line.
    assert notices == [Notice("Northern", Severity.WARNING, "Severe Delays", today[1])]
    assert api.calls[0].request.params == {"app_key": "key"}

    source.fetch(NOW)  # the stops' lines are only looked up once
    assert [c.request.url.split("?")[0].removeprefix(TFL_URL) for c in api.calls[3:]] == ["/Line/northern,12/Status"]


def test_line_status_only_shows_the_statuses_worth_a_notice(api):
    """The same for every mode: a suspended bus route is as severe as a suspended line."""
    api.add(responses.GET, f"{TFL_URL}/StopPoint/940GZZLUKNG", json=_stop("940GZZLUKNG", "NaptanMetroStation", ["12"]))
    statuses = {
        "12": (2, "Suspended"),
        "northern": (2, "Suspended"),
        "victoria": (9, "Minor Delays"),
        "68": (0, "Special Service"),
        "district": (20, "Service Closed"),
        "circle": (17, "Issues Reported"),
        "central": (5, "Part Closure"),
    }
    lines = [
        {
            "id": line_id,
            "name": line_id.title(),
            "modeName": "bus" if line_id.isdigit() else "tube",
            "lineStatuses": [_status(code, description)],
        }
        for line_id, (code, description) in statuses.items()
    ]
    api.add(responses.GET, f"{TFL_URL}/Line/12/Status", json=lines)

    source = TflLineStatusSource(requests.Session(), Tfl(app_key="", stop_ids=["940GZZLUKNG"]))

    assert source.fetch(NOW) == [
        Notice("Bus 12", Severity.SEVERE, "Suspended"),
        Notice("Northern", Severity.SEVERE, "Suspended"),
        Notice("Circle", Severity.INFO, "Issues Reported"),
        Notice("Central", Severity.PLANNED, "Part Closure"),
    ]


def test_line_status_shows_a_reason_once_for_routes_sharing_it(api):
    api.add(responses.GET, f"{TFL_URL}/StopPoint/940GZZLUKNG", json=_stop("940GZZLUKNG", "NaptanMetroStation", ["1"]))
    closure = (
        "WATERLOO ROAD, Southwark: Routes 1 68 and 188   cannot serve St. George's Circus. Use stops in Borough Road."
    )
    early, late = NOW + timedelta(hours=2), NOW + timedelta(hours=6)
    today = NOW - timedelta(hours=1)
    lines = [
        {
            "id": "1",
            "name": "1",
            "modeName": "bus",
            "lineStatuses": [_status(3, "Part Suspended", [(today, early)], closure)],
        },
        {
            "id": "68",
            "name": "68",
            "modeName": "bus",
            "lineStatuses": [_status(2, "Suspended", [(today, late)], closure)],
        },
        {
            "id": "188",
            "name": "188",
            "modeName": "bus",
            "lineStatuses": [_status(3, "Part Suspended", [(today, early)], closure)],
        },
    ]
    api.add(responses.GET, f"{TFL_URL}/Line/1/Status", json=lines)

    notices = TflLineStatusSource(requests.Session(), Tfl(app_key="", stop_ids=["940GZZLUKNG"])).fetch(NOW)

    # One notice for all three routes: the most severe status, the latest end.
    assert notices == [
        Notice(
            "Buses",
            Severity.SEVERE,
            "WATERLOO ROAD, Southwark: Routes 1 68 and 188 cannot serve St. George's Circus.",
            late,
        )
    ]


@pytest.mark.parametrize(
    ("reason", "line", "headline"),
    [
        (
            "Victoria Line: Severe delays due to a track fault at Brixton. GOOD SERVICE on the rest of the line.",
            "Victoria",
            "Severe delays due to a track fault at Brixton.",
        ),
        (
            (
                "CENTRAL LINE: Saturday 26 September, no service between Marble Arch and Loughton. "
                "Replacement buses operate."
            ),
            "Central",
            "Saturday 26 September, no service between Marble Arch and Loughton.",
        ),
        (
            "DOCKLANDS LIGHT RAILWAY: Sunday 27 September, no service between Shadwell and Tower Gateway.",
            "DLR",
            "Sunday 27 September, no service between Shadwell and Tower Gateway.",
        ),
        (
            "Waterloo & City line: service operates Monday to Friday only.",
            "Waterloo & City",
            "Service operates Monday to Friday only.",
        ),
        ("no full stop and no prefix", "Northern", "No full stop and no prefix"),
    ],
)
def test_line_status_headline_is_the_first_sentence_without_the_line_name(api, reason, line, headline):
    api.add(responses.GET, f"{TFL_URL}/StopPoint/940GZZLUKNG", json=_stop("940GZZLUKNG", "NaptanMetroStation", ["x"]))
    lines = [
        {"id": "x", "name": line, "modeName": "tube", "lineStatuses": [_status(6, "Severe Delays", reason=reason)]}
    ]
    api.add(responses.GET, f"{TFL_URL}/Line/x/Status", json=lines)

    [notice] = TflLineStatusSource(requests.Session(), Tfl(app_key="", stop_ids=["940GZZLUKNG"])).fetch(NOW)

    assert (notice.source, notice.text) == (line, headline)


def test_station_notices_are_shortened_deduplicated_and_expire(api):
    points = [
        {
            "commonName": "Elephant & Castle Underground Station",
            "description": "ELEPHANT & CASTLE UNDERGROUND STATION: The lifts are out of service.",
            "toDate": (NOW + timedelta(days=5)).isoformat(),
        },
        {
            "commonName": "Elephant & Castle Underground Station",
            "description": "Elephant & Castle: The lifts are out of service.",
            "toDate": (NOW + timedelta(days=2)).isoformat(),
        },
        {
            "commonName": "Kennington Underground Station",
            "description": "Kennington: Entrance closed.",
            "toDate": (NOW - timedelta(hours=1)).isoformat(),
        },
    ]
    api.add(responses.GET, f"{TFL_URL}/StopPoint/940GZZLUKNG,490000077E/Disruption", json=points)

    notices = TflStationSource(requests.Session(), TFL).fetch(NOW)

    assert [n.text for n in notices] == ["Elephant & Castle: The lifts are out of service."]


def test_roads_only_reports_serious_incidents_nearby(api):
    near, far = "[-0.098,51.492]", "[-0.3,51.6]"
    disruptions = [
        {"id": "1", "severity": "Severe", "category": "Collisions", "comments": "[A2] Old Kent Road", "point": near},
        {"id": "2", "severity": "Serious", "category": "Works", "comments": "[A13] Commercial Road", "point": far},
        {"id": "3", "severity": "Serious", "category": "Works", "comments": "No location"},
    ]
    api.add(responses.GET, f"{TFL_URL}/Road/all/Disruption", json=disruptions)

    notices = TflRoadSource(requests.Session(), TFL, SOUTHWARK).fetch(NOW)

    assert notices == [Notice("Roads", Severity.WARNING, "Collisions: [A2] Old Kent Road")]
    assert api.calls[0].request.params["severities"] == "Severe,Serious"


# --- Bank holidays and the clocks -------------------------------------------------

BANK_HOLIDAYS = {
    division: {
        "division": division,
        "events": [
            {"title": "Christmas Day", "date": "2026-12-25", "notes": "", "bunting": True},
            {"title": "Boxing Day", "date": "2026-12-28", "notes": "Substitute day", "bunting": True},
        ]
        + (
            [{"title": "St Andrew’s Day", "date": "2026-11-30", "notes": "", "bunting": True}]  # noqa: RUF001 -- the apostrophe gov.uk's bank holidays use
            if division == "scotland"
            else []
        ),
    }
    for division in ("england-and-wales", "scotland", "northern-ireland")
}


@pytest.mark.parametrize(
    ("now", "country", "expected"),
    [
        (datetime(2026, 11, 24, 12, tzinfo=UTC), None, []),  # Christmas is over a month away
        (datetime(2026, 11, 26, 12, tzinfo=UTC), None, ["Fri 25 Dec: Christmas Day"]),
        (datetime(2026, 11, 26, 12, tzinfo=UTC), "Scotland", ["Mon 30 Nov: St Andrew’s Day"]),  # noqa: RUF001 -- the apostrophe gov.uk's bank holidays use
        (datetime(2026, 12, 24, 12, tzinfo=UTC), "Wales", ["Tomorrow: Christmas Day"]),
        (datetime(2026, 12, 28, 9, tzinfo=UTC), None, ["Today: Boxing Day (substitute day)"]),
    ],
)
def test_bank_holiday_shows_the_next_one_less_than_a_month_away(api, now, country, expected):
    api.add(responses.GET, BANK_HOLIDAYS_URL, json=BANK_HOLIDAYS)

    notices = BankHolidaySource(requests.Session(), country).fetch(now)

    assert [n.text for n in notices] == expected
    assert all(n.severity == Severity.REMINDER for n in notices)


def test_bank_holiday_ends_at_midnight_after_it(api):
    api.add(responses.GET, BANK_HOLIDAYS_URL, json=BANK_HOLIDAYS)

    [notice] = BankHolidaySource(requests.Session()).fetch(datetime(2026, 12, 20, tzinfo=UTC))

    assert notice.valid_until == datetime(2026, 12, 26, tzinfo=UTC)  # GMT in December


def test_bank_holidays_are_downloaded_once_a_day(api):
    api.add(responses.GET, BANK_HOLIDAYS_URL, json=BANK_HOLIDAYS)
    source = BankHolidaySource(requests.Session())

    source.fetch(NOW)
    source.fetch(NOW + timedelta(hours=23))
    assert len(api.calls) == 1

    source.fetch(NOW + timedelta(days=1))
    assert len(api.calls) == 2


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 25, 12, tzinfo=UTC), []),  # a month out
        (datetime(2026, 10, 20, 12, tzinfo=UTC), ["Clocks go back an hour on Sun 25 Oct, at 02:00"]),
        (datetime(2026, 10, 24, 18, tzinfo=UTC), ["Clocks go back an hour tonight, at 02:00"]),
        (datetime(2026, 10, 25, 3, tzinfo=UTC), []),  # already changed
        (datetime(2027, 3, 22, 12, tzinfo=UTC), ["Clocks go forward an hour on Sun 28 Mar, at 01:00"]),
    ],
)
def test_clock_change_is_mentioned_in_the_week_before(now, expected):
    notices = ClockChangeSource(requests.Session()).fetch(now)

    assert [f"{n.text} {format_clock(n.display_time)}" for n in notices] == expected
    assert all(n.severity == Severity.REMINDER for n in notices)


def test_clock_change_notice_ends_when_the_clocks_change():
    [notice] = ClockChangeSource(requests.Session()).fetch(datetime(2026, 10, 24, 18, tzinfo=UTC))

    assert notice.valid_until == datetime(2026, 10, 25, 1, tzinfo=UTC)


def test_reminders_rank_below_current_problems_but_above_planned_works():
    ranked = sorted([Severity.PLANNED, Severity.REMINDER, Severity.INFO, Severity.SEVERE])

    assert ranked == [Severity.SEVERE, Severity.INFO, Severity.REMINDER, Severity.PLANNED]


# --- The board -------------------------------------------------------------------


class FakeSource(NoticeSource):
    refresh_interval = timedelta(minutes=10)

    def __init__(self, name: str, *answers: list[Notice] | Exception):
        super().__init__(requests.Session())
        self.name = name
        self.answers = list(answers)
        self.calls = 0

    def fetch(self, now: datetime) -> list[Notice]:
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = NOW

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return Clock.now

    monkeypatch.setattr(board_module, "datetime", FrozenDatetime)
    return Clock


def board_settings(postcode: str | None = None, stop_ids: list[str] | None = None) -> NoticeBoardSettings:
    stops = tuple(DepartureStop(source="tfl", id=stop_id) for stop_id in stop_ids or [])
    return NoticeBoardSettings(postcode=postcode, departures=DeparturesSettings(stops))


def board_with(*sources: NoticeSource) -> NoticeBoardClient:
    client = NoticeBoardClient(REGISTRATION, board_settings(stop_ids=["940GZZLUKNG"]))
    client.sources = list(sources)
    return client


def update(client: NoticeBoardClient):
    return asyncio.run(client.update())


def test_board_ranks_by_severity_then_source_order(clock):
    minor = Notice("TfL", Severity.INFO, "Victoria: Minor Delays")
    severe = Notice("TfL", Severity.SEVERE, "Northern: Suspended")
    amber = Notice("Met Office", Severity.WARNING, "Amber warning: wind")
    alert = Notice("Floods", Severity.INFO, "Flood alert: Effra")

    panel = update(
        board_with(FakeSource("tfl", [minor, severe]), FakeSource("met", [amber]), FakeSource("ea", [alert]))
    )

    assert isinstance(panel, NoticeBoardPanel)
    assert panel.notices == [severe, amber, minor, alert]


def test_board_is_empty_when_nothing_is_reported(clock):
    """All clear: an empty board, which the layout leaves out to give the stops more room."""
    panel = update(board_with(FakeSource("tfl", [])))

    assert isinstance(panel, NoticeBoardPanel)
    assert panel.notices == []
    assert panel.fitted_height(155) == 0


def test_board_only_asks_a_source_again_after_its_refresh_interval(clock):
    source = FakeSource("tfl", [])
    client = board_with(source)

    update(client)
    clock.now += timedelta(minutes=9)
    update(client)
    assert source.calls == 1

    clock.now += timedelta(minutes=1)
    update(client)
    assert source.calls == 2


def test_board_keeps_a_failing_sources_last_notices_until_they_go_stale(clock):
    delay = Notice("TfL", Severity.INFO, "Victoria: Minor Delays")
    flaky = FakeSource("tfl", [delay], ConnectionError("down"))
    client = board_with(flaky, FakeSource("met", []))

    update(client)
    clock.now += timedelta(minutes=10)
    assert update(client).notices == [delay]  # the failure keeps the last answer

    clock.now += timedelta(minutes=51)
    assert update(client).notices == []  # over an hour old: dropped


def test_board_drops_notices_past_their_end(clock):
    ending = Notice("TfL", Severity.INFO, "Station closed", valid_until=NOW + timedelta(minutes=5))
    client = board_with(FakeSource("tfl", [ending]))

    assert update(client).notices == [ending]
    clock.now += timedelta(minutes=6)
    assert update(client).notices == []


def test_board_errors_rather_than_claiming_all_clear_when_no_source_answered(clock):
    client = board_with(FakeSource("tfl", ConnectionError("down")))

    with pytest.raises(RuntimeError):
        update(client)


def test_board_with_nothing_configured_still_has_bank_holidays_and_the_clocks():
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode=None))

    assert not client.is_disabled()
    assert [type(s) for s in client.sources] == [ClockChangeSource, BankHolidaySource]
    assert client.sources[1].division == "england-and-wales"


def test_board_adds_local_sources_once_the_postcode_is_found(api, clock):
    result = SOUTHWARK.model_dump()
    api.add(responses.GET, f"{POSTCODES_URL}/SE17%203LL", json={"result": result})
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))
    client._refresh = lambda source, now: None  # only the lookup is under test here

    with pytest.raises(RuntimeError):  # sources added, but none has answered yet
        update(client)

    assert [type(s) for s in client.sources] == [
        ClockChangeSource,
        BankHolidaySource,
        MetOfficeWarningsSource,
        FloodWarningsSource,
        TflRoadSource,
    ]


def test_board_logs_that_it_resolved_postcode(api, clock, caplog):
    api.add(responses.GET, f"{POSTCODES_URL}/SE17%203LL", json={"result": SOUTHWARK.model_dump()})
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))
    client._refresh = lambda source, now: None

    with caplog.at_level("INFO", logger="countdown_core.notices.notice_board_client"), pytest.raises(RuntimeError):
        update(client)

    assert (
        "Notice board: location lookup succeeded: London, Southwark; Met Office region se, "
        "bank holidays for england-and-wales" in caplog.messages
    )


def test_board_with_an_unknown_postcode_says_so(api, clock, caplog):
    api.add(responses.GET, f"{POSTCODES_URL}/ZZ1%201ZZ", status=404, json={"error": "Invalid postcode"})
    api.add(responses.GET, BANK_HOLIDAYS_URL, json=BANK_HOLIDAYS)

    with caplog.at_level("WARNING", logger="countdown_core.notices.notice_board_client"):
        panel = update(NoticeBoardClient(REGISTRATION, board_settings(postcode="ZZ1 1ZZ")))

    assert panel.message == "Unknown postcode"
    # Where the board is stays out of the logs.
    assert caplog.messages == ["The configured postcode isn't a known UK postcode; no local notices"]


def test_board_retries_a_postcode_lookup_that_failed(api, clock, caplog):
    api.add(responses.GET, f"{POSTCODES_URL}/SE17%203LL", status=503)
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))
    client.session = requests.Session()  # no retries, so the 503 comes straight back

    with (
        caplog.at_level("WARNING", logger="countdown_core.notices.notice_board_client"),
        pytest.raises(RuntimeError, match=r"^Couldn't look up the postcode yet$"),
    ):
        update(client)
    assert client.location is None
    assert client.postcode == "SE17 3LL"  # still to be looked up, unlike an unknown one
    # Not the HTTPError's own text either: that quotes the URL, postcode and all.
    assert caplog.messages == ["Couldn't look up the postcode: HTTPError"]


# --- Config ----------------------------------------------------------------------


def test_notice_board_gets_the_tfl_stops_and_the_brokers_postcode():
    config = make_config(tfl={"stop_ids": ["940GZZLUKNG"]}, notice_board={"postcode": "SE17 3LL"})

    settings = section_for("notice_board", config)

    assert settings.tfl.stop_ids == ["940GZZLUKNG"]
    assert settings.postcode == "SE17 3LL"


def test_notice_board_has_no_postcode_unless_the_broker_sends_one():
    settings = section_for("notice_board", make_config(tfl={"stop_ids": ["940GZZLUKNG"]}))

    assert settings.postcode is None
    assert settings.tfl.stop_ids == ["940GZZLUKNG"]


def test_a_change_to_either_the_postcode_or_the_stops_changes_the_notice_boards_settings():
    """AbstractClient.needs_refresh compares them, so the board is rebuilt for either."""

    def settings_for(stop_id: str, postcode: str) -> NoticeBoardSettings:
        return section_for(
            "notice_board", make_config(tfl={"stop_ids": [stop_id]}, notice_board={"postcode": postcode})
        )

    assert settings_for("a", "SE17 3LL") == settings_for("a", "SE17 3LL")
    assert settings_for("a", "SE17 3LL") != settings_for("b", "SE17 3LL")
    assert settings_for("a", "SE17 3LL") != settings_for("a", "E1 6AN")


# --- Panel -----------------------------------------------------------------------


def test_panel_says_how_many_notices_did_not_fit():
    notices = [Notice("TfL", Severity.INFO, f"Line {i}: Minor Delays") for i in range(10)]

    img = NoticeBoardPanel(notices).render(536, 155)

    # Five rows fit: four notices and a "+6 more" -- so nothing drawn below the fifth row.
    assert img.convert("L").crop((0, 140, 536, 155)).getextrema() == (255, 255)


def test_panel_is_only_as_tall_as_its_rows():
    def board(count: int) -> NoticeBoardPanel:
        return NoticeBoardPanel([Notice("TfL", Severity.INFO, "Victoria: Minor Delays")] * count)

    assert board(3).fitted_height(155) - board(2).fitted_height(155) == ROW_HEIGHT
    # Two rows drawn in it, both in full: nothing says "+N more".
    img = Image.new("RGB", (536, 155), "white")
    img.paste(board(2).render(536, board(2).fitted_height(155)))
    assert _drawn_rows(img) == 2


def test_panel_with_more_notices_than_fit_takes_all_the_room_it_is_given():
    notices = [Notice("TfL", Severity.INFO, f"Line {i}: Minor Delays") for i in range(10)]

    assert NoticeBoardPanel(notices).fitted_height(155) == 155


def test_panel_with_no_notices_needs_no_room():
    assert NoticeBoardPanel([]).fitted_height(155) == 0


@pytest.mark.parametrize("style", list(STYLES))
def test_every_style_leaves_room_for_its_title_and_frame(style):
    def board(count: int) -> NoticeBoardPanel:
        return NoticeBoardPanel([Notice("TfL", Severity.INFO, "Victoria: Minor Delays")] * count, style)

    assert board(2).fitted_height(155) == STYLES[style].top + 2 * ROW_HEIGHT + STYLES[style].bottom
    assert board(3).fitted_height(155) - board(2).fitted_height(155) == ROW_HEIGHT
    assert board(0).fitted_height(155) == 0


@pytest.mark.parametrize("style", list(STYLES))
def test_every_style_fits_five_rows_in_the_landscape_slot(style):
    """155 is what the landscape layout gives the board beside the energy chart: four notices and "+6 more"."""
    notices = [Notice("TfL", Severity.INFO, f"Line {i}: Minor Delays") for i in range(10)]
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(draw, xy, text, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ImageDraw.ImageDraw, "text", record)
        NoticeBoardPanel(notices, style).render(536, 155)

    assert sum(text.startswith("Line ") for text in drawn) == 4
    assert "+6 more" in drawn


def _drawn_rows(img) -> int:
    """How many of the panel's rows have something in them."""
    grey = img.convert("L")
    top = STYLES["hero"].top  # below the title and its rule
    return sum(
        grey.crop((0, top + i * 22, grey.width, top + (i + 1) * 22)).getextrema() != (255, 255) for i in range(5)
    )


def test_panel_fills_leftover_rows_with_planned_works():
    current = [Notice("Victoria", Severity.WARNING, "Severe delays")]
    planned = [Notice("Central", Severity.PLANNED, f"Closure {i}") for i in range(10)]

    img = NoticeBoardPanel(current + planned).render(536, 155)

    assert _drawn_rows(img) == 5  # one current notice, then four planned: no "+N more"


def test_panel_never_lets_planned_works_push_out_current_problems():
    current = [Notice("TfL", Severity.INFO, f"Line {i}: Issues Reported") for i in range(4)]
    planned = [Notice("Central", Severity.PLANNED, "Closure")] * 3
    only_current = NoticeBoardPanel(current).render(536, 155)

    img = NoticeBoardPanel(current + planned).render(536, 155)

    # The fifth row gets one planned notice; the other two are simply left off.
    assert _drawn_rows(only_current) == 4
    assert _drawn_rows(img) == 5


def test_panel_shows_reminders_as_normal_rows_ahead_of_planned_works():
    current = [Notice("TfL", Severity.INFO, f"Line {i}: Issues Reported") for i in range(4)]
    reminder = Notice("Clocks", Severity.REMINDER, "Clocks go back an hour tonight, at 2am")
    planned = Notice("Central", Severity.PLANNED, "Closure")

    img = NoticeBoardPanel([*current, reminder, planned]).render(536, 155)
    with_planned_only = NoticeBoardPanel([*current, planned]).render(536, 155)

    # The reminder takes the last row; with no reminder, the planned closure would have.
    assert _drawn_rows(img) == 5
    assert img.crop((0, 118, 536, 140)).tobytes() != with_planned_only.crop((0, 118, 536, 140)).tobytes()
