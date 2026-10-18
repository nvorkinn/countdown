import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import requests
import responses
from config_factory import make_config
from PIL import Image, ImageDraw
from redis.exceptions import ConnectionError as RedisConnectionError
from test_utils import REGISTRATION

from countdown_core.core import moment
from countdown_core.core.api_registry import section_for
from countdown_core.notices import notice_board_client as board_module
from countdown_core.notices.calendar import ClockChangeSource, StoredBankHolidaySource
from countdown_core.notices.floods import StoredFloodWarningsSource
from countdown_core.notices.met_office import StoredMetOfficeWarningsSource, region_for, warning_notices
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.notices.notice_board_panel import ROW_HEIGHT, NoticeBoardPanel
from countdown_core.notices.notice_styles import STYLES
from countdown_core.notices.tfl import NoStoredTflNoticesError, StoredTflRoadSource
from countdown_core.store import keys
from countdown_core.store.bank_holidays import StoredBankHolidays
from countdown_core.store.location import StoredLocation
from countdown_core.tfl.models import Postcode
from countdown_core.utils.clock import format_clock
from countdown_providers.met_office.main import feed_items

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
SOUTHWARK = Postcode(
    postcode="SE17 3LL",
    latitude=51.49,
    longitude=-0.1,
    country="England",
    region="London",
    admin_district="Southwark",
)


# --- Met Office ------------------------------------------------------------------


def parse_warnings(feed: bytes, now: datetime) -> list[Notice]:
    """Return the notices the board makes, as of `now`, from `feed` as provider-met-office stores it."""
    return warning_notices(feed_items(feed), now)


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


def test_met_office_logs_neither_the_postcode_nor_its_area_when_it_has_no_region(caplog):
    location = SOUTHWARK.model_copy(update={"country": "Scotland", "region": None, "admin_district": "Somewhere New"})

    with caplog.at_level("WARNING", logger="countdown_core.notices.met_office"):
        region_for(location)

    assert caplog.messages == ["No Met Office warning region for a postcode's area; using the whole UK"]


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

    # The moment the board draws at (core.moment), as a render sets it.
    monkeypatch.setattr(moment, "now", lambda: FrozenDatetime.now(UTC))
    return Clock


def board_settings(postcode: str | None = None) -> NoticeBoardSettings:
    return NoticeBoardSettings(postcode=postcode)


def board_with(*sources: NoticeSource) -> NoticeBoardClient:
    client = NoticeBoardClient(REGISTRATION, board_settings())
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


def test_board_ranks_the_other_clients_notices_with_its_own(clock):
    """Theirs come first, so a delay at the stops ranks above the place's notices of the same severity."""
    alert = Notice("Floods", Severity.INFO, "Flood alert: Effra")
    minor = Notice("TfL", Severity.INFO, "Victoria: Minor Delays")
    client = board_with(FakeSource("ea", [alert]))
    client.use_client_sources([FakeSource("tfl", [minor])])

    assert update(client).notices == [minor, alert]


def test_board_drops_the_notices_of_a_client_source_it_is_no_longer_given(clock):
    old = FakeSource("entur", [Notice("Line 5", Severity.WARNING, "Bus for metro")])
    new = FakeSource("tfl", [])
    client = board_with()
    client.use_client_sources([old])
    update(client)

    client.use_client_sources([new])
    clock.now += timedelta(minutes=1)

    assert update(client).notices == []
    assert old.calls == 1


def test_board_is_empty_when_nothing_is_reported(clock):
    """All clear: an empty board, which the layout leaves out to give the stops more room."""
    panel = update(board_with(FakeSource("tfl", [])))

    assert isinstance(panel, NoticeBoardPanel)
    assert panel.notices == []
    assert panel.fitted_height(155) == 0


def test_board_reads_every_source_on_every_poll(clock):
    """They're all reads of the store or sums worked out here, so there's nothing to save by asking less often."""
    source = FakeSource("tfl", [])
    client = board_with(source)

    for _ in range(3):
        update(client)
        clock.now += timedelta(seconds=20)

    assert source.calls == 3


def test_board_leaves_out_a_source_whose_key_has_expired(clock, caplog):
    """No last notices are kept: once the provider's key has expired, the delay it reported is off the board."""
    delay = Notice("TfL", Severity.INFO, "Victoria: Minor Delays")
    expiring = FakeSource("tfl", [delay], NoStoredTflNoticesError("Nothing in the store at v1:notices:tfl-lines:x"))
    client = board_with(expiring, FakeSource("met", []))

    assert update(client).notices == [delay]
    clock.now += timedelta(minutes=1)
    with caplog.at_level("WARNING", logger=board_module.__name__):
        assert update(client).notices == []  # the other source answered: all clear, as far as is known

    [record] = caplog.records
    assert record.getMessage() == "tfl: Nothing in the store at v1:notices:tfl-lines:x"
    assert not record.exc_info  # expected while a provider is down: no traceback


def test_board_leaves_out_a_source_that_fails_and_keeps_none_of_its_notices(clock, caplog):
    delay = Notice("TfL", Severity.INFO, "Victoria: Minor Delays")
    broken = FakeSource("tfl", [delay], ValueError("a provider on another version"))
    client = board_with(broken, FakeSource("met", []))

    update(client)
    with caplog.at_level("WARNING", logger=board_module.__name__):
        assert update(client).notices == []

    assert caplog.records[0].exc_info  # unexpected: with its traceback


def test_board_shows_what_the_sources_still_there_say_when_another_has_expired(clock):
    amber = Notice("Met Office", Severity.WARNING, "Amber warning: wind")
    client = board_with(FakeSource("tfl", NoStoredTflNoticesError("gone")), FakeSource("met", [amber]))

    assert update(client).notices == [amber]


def test_board_says_not_available_rather_than_all_clear_when_every_sources_key_has_expired(clock):
    """The local sources (the clocks) can't vouch for the rest: "All clear" would be a guess."""
    client = board_with(
        ClockChangeSource(requests.Session()),
        FakeSource("tfl", NoStoredTflNoticesError("gone")),
        FakeSource("met", NoStoredTflNoticesError("gone too")),
    )

    panel = update(client)

    assert (panel.title, panel.message) == ("Notices", "Not available")


def test_board_raises_when_redis_does_not_answer_so_the_registry_keeps_the_last_board(clock):
    """Redis being unreachable isn't the data expiring: nothing is dropped for it."""
    amber = Notice("Met Office", Severity.WARNING, "Amber warning: wind")
    client = board_with(FakeSource("met", [amber]), FakeSource("tfl", RedisConnectionError("Timeout reading")))

    with pytest.raises(RedisConnectionError):
        update(client)


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
    assert [type(s) for s in client.sources] == [ClockChangeSource, StoredBankHolidaySource]
    assert client.sources[1].division == "england-and-wales"


def put_location(store, postcode: str, location: Postcode | None) -> None:
    """Store where `postcode` is, as provider-locations would: `location`, or None for a postcode that doesn't exist."""
    stored = StoredLocation(fetched_at=NOW, postcode=postcode, found=location is not None, location=location)
    store.set(keys.location(postcode), stored.model_dump_json())


def test_board_adds_local_sources_once_the_store_says_where_the_postcode_is(store, clock):
    put_location(store, "SE17 3LL", SOUTHWARK)
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))
    client._read = lambda sources, now: ({}, set())  # only placing the postcode is under test here

    with pytest.raises(RuntimeError):  # sources added, but none has answered yet
        update(client)

    assert client.location == SOUTHWARK
    assert [type(s) for s in client.sources] == [
        ClockChangeSource,
        StoredBankHolidaySource,
        StoredMetOfficeWarningsSource,
        StoredFloodWarningsSource,
        StoredTflRoadSource,
    ]
    # The floods are read from the store, under the postcode's place.
    assert client.sources[3].key == "v1:notices:floods:SE173LL"


def test_board_reads_the_location_under_its_postcodes_place_however_its_written(store, clock):
    put_location(store, "SE17 3LL", SOUTHWARK)
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="se17 3ll"))

    client._ensure_location(NOW)

    assert client.location == SOUTHWARK


def test_board_logs_that_it_placed_the_postcode_but_not_where(store, clock, caplog):
    put_location(store, "SE17 3LL", SOUTHWARK)
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))
    client._read = lambda sources, now: ({}, set())

    with caplog.at_level("DEBUG"), pytest.raises(RuntimeError):
        update(client)

    assert "Notice board: found the postcode's location; reading its local notices" in caplog.messages
    # Nor anything worked out from it: its area, its Met Office region, its bank holidays' division.
    for trace in ("SE17", "SE173LL", "London", "Southwark", "51.49", "england-and-wales", "region se"):
        assert trace not in caplog.text


def test_board_with_an_unknown_postcode_says_so(store, clock, caplog):
    put_location(store, "ZZ1 1ZZ", None)
    store.set(keys.bank_holidays(), StoredBankHolidays(fetched_at=NOW, divisions=BANK_HOLIDAYS).model_dump_json())
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="ZZ1 1ZZ"))

    with caplog.at_level("WARNING", logger="countdown_core.notices.notice_board_client"):
        panel = update(client)

    assert panel.message == "Unknown postcode"
    assert [type(s) for s in client.sources] == [ClockChangeSource, StoredBankHolidaySource]
    # Where the board is stays out of the logs.
    assert caplog.messages == ["The configured postcode isn't a known UK postcode; no local notices"]


def test_board_waits_for_a_location_not_in_the_store_yet_and_reads_again_a_poll_later(store, clock, caplog):
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))
    client._read = lambda sources, now: ({}, set())

    with caplog.at_level("WARNING", logger="countdown_core.notices.notice_board_client"):
        panel = update(client)  # its key isn't there: the place's notices aren't available
    assert panel.message == "Not available"
    assert client.location is None
    assert client.postcode == "SE17 3LL"  # still to be placed, unlike an unknown one
    assert not client.unknown_postcode
    # Not the key: it has the postcode in it.
    assert caplog.messages == ["No location in the store for the board's postcode yet (is provider-locations running?)"]

    put_location(store, "SE17 3LL", SOUTHWARK)  # provider-locations gets to it
    clock.now += board_module.LOCATION_RETRY - timedelta(seconds=1)
    client._ensure_location(clock.now)
    assert client.location is None  # not read again yet
    clock.now += timedelta(seconds=1)
    client._ensure_location(clock.now)
    assert client.location == SOUTHWARK


def test_board_whose_store_cant_be_read_tries_again_without_naming_the_postcode(store, clock, caplog, monkeypatch):
    def unreachable(key):
        msg = f"couldn't GET {key}"  # as a real one could say: the key, postcode and all
        raise ConnectionError(msg)

    monkeypatch.setattr(store, "get", unreachable)
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))
    client._read = lambda sources, now: ({}, set())

    with (
        caplog.at_level("WARNING", logger="countdown_core.notices.notice_board_client"),
        pytest.raises(RuntimeError, match=r"^Couldn't look up the postcode yet$"),
    ):
        update(client)

    assert caplog.messages == ["Couldn't read the postcode's location from the store: ConnectionError"]
    assert client.postcode == "SE17 3LL"


def test_board_asks_nothing_over_http_to_place_its_postcode(store, clock):
    put_location(store, "SE17 3LL", SOUTHWARK)
    client = NoticeBoardClient(REGISTRATION, board_settings(postcode="SE17 3LL"))

    with responses.RequestsMock():  # any request would fail
        client._ensure_location(NOW)

    assert client.location == SOUTHWARK


# --- Config ----------------------------------------------------------------------


def test_notice_board_gets_the_brokers_postcode():
    config = make_config(tfl={"stop_ids": ["940GZZLUKNG"]}, notice_board={"postcode": "SE17 3LL"})

    settings = section_for("notice_board", config)

    assert settings == NoticeBoardSettings(postcode="SE17 3LL")  # the stops aren't the board's: see the departures


def test_notice_board_has_no_postcode_unless_the_broker_sends_one():
    assert section_for("notice_board", make_config()).postcode is None


def test_a_change_to_the_postcode_but_not_the_stops_changes_the_notice_boards_settings():
    """AbstractClient.needs_refresh compares them; the stops' disruptions come from the departures client instead."""

    def settings_for(stop_id: str, postcode: str) -> NoticeBoardSettings:
        return section_for(
            "notice_board", make_config(tfl={"stop_ids": [stop_id]}, notice_board={"postcode": postcode})
        )

    assert settings_for("a", "SE17 3LL") == settings_for("b", "SE17 3LL")
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
