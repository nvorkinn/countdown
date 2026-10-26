"""A Norwegian screen: its postcode, holidays, weather warnings, Entur disruptions, time zone and language."""

from datetime import UTC, date, datetime

import pytest
import requests
import responses
from test_utils import TEST_DEVICE_ID

from countdown_api.models import DepartureStop
from countdown_renderer.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_renderer.notices.calendar import ClockChangeSource
from countdown_renderer.notices.entur import StoredEnturSituationsSource
from countdown_renderer.notices.norway import NorwegianHolidaySource, norwegian_holidays, parse_metalerts
from countdown_renderer.notices.notice import Severity
from countdown_renderer.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_renderer.utils.clock import format_clock
from countdown_shared import i18n
from countdown_shared.notices.norway_feed import ADDRESSES_URL, lookup_norwegian_postcode
from countdown_shared.store import keys
from countdown_shared.store.location import StoredLocation
from countdown_shared.tfl.models import Postcode

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
OSLO = Postcode(postcode="0150", latitude=59.9116, longitude=10.7505, country="Norway", admin_district="Oslo")


@pytest.fixture
def api():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def oslo_screen():
    i18n.set_locale("en", "Europe/Oslo")
    yield
    i18n.set_locale("en", "Europe/London")


def test_a_postcode_is_placed_by_kartverket(api):
    api.get(
        ADDRESSES_URL,
        json={
            "adresser": [
                {
                    "postnummer": "0150",
                    "poststed": "OSLO",
                    "kommunenavn": "OSLO",
                    "representasjonspunkt": {"epsg": "EPSG:4258", "lat": 59.9116, "lon": 10.7505},
                }
            ]
        },
    )
    assert lookup_norwegian_postcode(requests.Session(), "0150") == OSLO
    assert "postnummer=0150" in api.calls[0].request.url


def test_a_postcode_kartverket_has_no_address_for_is_unknown(api):
    api.get(ADDRESSES_URL, json={"adresser": []})
    assert lookup_norwegian_postcode(requests.Session(), "0001") is None


def test_the_holidays_follow_easter():
    holidays = norwegian_holidays(2027)  # Easter Sunday is 28 March 2027
    for day, name in [
        (date(2027, 3, 25), "Maundy Thursday"),
        (date(2027, 3, 29), "Easter Monday"),
        (date(2027, 5, 6), "Ascension Day"),
        (date(2027, 5, 16), "Whit Sunday"),
        # Whit Monday falls on Constitution Day this year: both are there.
        (date(2027, 5, 17), "Constitution Day"),
        (date(2027, 5, 17), "Whit Monday"),
    ]:
        assert (day, name) in holidays


def test_the_next_holiday_is_mentioned_once_less_than_a_month_away():
    source = NorwegianHolidaySource(requests.Session())
    assert source.fetch(NOW) == []
    [notice] = source.fetch(datetime(2026, 12, 1, 12, tzinfo=UTC))
    assert (notice.source, notice.severity, notice.text) == (
        "Public holiday",
        Severity.REMINDER,
        "Fri 25 Dec: Christmas Day",
    )


def test_the_new_year_is_found_across_the_year_end():
    [notice] = NorwegianHolidaySource(requests.Session()).fetch(datetime(2026, 12, 31, 12, tzinfo=UTC))
    assert notice.text == "Tomorrow: New Year's Day"


METALERTS = {
    "features": [
        {
            "properties": {"riskMatrixColor": "Orange", "eventAwarenessName": "Kraftig vind", "event": "wind"},
            "when": {"interval": ["2026-10-16T06:00:00+00:00", "2026-10-16T19:00:00+00:00"]},
        },
        {
            "properties": {"awareness_level": "2; yellow; Moderate", "eventAwarenessName": "Regn"},
            "when": {"interval": ["2026-10-16T06:00:00+00:00", "2026-10-17T06:00:00+00:00"]},
        },
        {
            "properties": {"riskMatrixColor": "Yellow", "eventAwarenessName": "Is"},
            "when": {"interval": ["2026-10-15T06:00:00+00:00", "2026-10-15T09:00:00+00:00"]},
        },
    ]
}


def test_metalerts_become_notices_until_their_end_in_oslo_time():
    orange, yellow = parse_metalerts(METALERTS, NOW)
    assert (orange.source, orange.severity, orange.text) == (
        "MET",
        Severity.WARNING,
        "Orange warning: kraftig vind until Fri",
    )
    # The board adds the time, in the screen's clock format: "until Fri 21:00".
    assert format_clock(orange.display_time) == "21:00"
    assert (yellow.severity, yellow.text, format_clock(yellow.display_time)) == (
        Severity.INFO,
        "Yellow warning: regn until Sat",
        "08:00",
    )


def test_the_clocks_change_in_the_screens_time_zone(oslo_screen):
    [notice] = ClockChangeSource(requests.Session()).fetch(datetime(2026, 10, 24, 18, tzinfo=UTC))
    # 01:00 UTC is 03:00 in Oslo, before the clocks go back to 02:00.
    assert notice.text == "Clocks go back an hour tonight, at"
    assert format_clock(notice.display_time, "12h") == "3:00 AM"


def board(postcode: str | None, country: str | None) -> NoticeBoardClient:
    return NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(postcode, country))


def test_a_norwegian_postcode_brings_norways_sources(store):
    # Where provider-locations placed it, from Kartverket.
    stored = StoredLocation(fetched_at=NOW, postcode="0150", found=True, location=OSLO)
    store.set(keys.location("0150"), stored.model_dump_json())
    client = board("0150", "NO")
    client._ensure_location(NOW)
    names = [type(source).__name__ for source in client.sources]
    assert names == ["ClockChangeSource", "NorwegianHolidaySource", "StoredMetAlertsSource"]
    assert client.sources[-1].location == OSLO  # the warnings covering it


def test_a_norwegian_postcode_kartverket_doesnt_know_gets_norways_holidays_alone(store):
    stored = StoredLocation(fetched_at=NOW, postcode="0001", found=False)
    store.set(keys.location("0001"), stored.model_dump_json())
    client = board("0001", "NO")
    client._ensure_location(NOW)
    assert [type(source).__name__ for source in client.sources] == ["ClockChangeSource", "NorwegianHolidaySource"]
    assert client.unknown_postcode


def test_norwegian_stops_bring_their_disruptions_whatever_the_postcode():
    client = DeparturesClient(TEST_DEVICE_ID, DeparturesSettings((DepartureStop(source="entur", id="NSR:Quay:7333"),)))
    [source] = client.notice_sources()
    assert isinstance(source, StoredEnturSituationsSource)
    assert source.stop_ids == ["NSR:Quay:7333"]


def test_a_screen_on_oslo_time_without_a_postcode_gets_norways_holidays(oslo_screen):
    assert isinstance(board(None, None).sources[-1], NorwegianHolidaySource)


def test_text_is_drawn_in_the_screens_language(tmp_path, monkeypatch):
    (tmp_path / "nb.po").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=utf-8\\n"\n'
        '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
        'msgid "due"\nmsgstr "nå"\n\n'
        'msgid "{n} min"\nmsgid_plural "{n} mins"\nmsgstr[0] "{n} min"\nmsgstr[1] "{n} min"\n\n'
        'msgid "Not translated yet"\nmsgstr ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    i18n._catalogue.cache_clear()
    i18n.set_locale("nb", "Europe/Oslo")
    try:
        assert i18n._("due") == "nå"
        assert i18n.ngettext("{n} min", "{n} mins", 3).format(n=3) == "3 min"
        assert i18n._("Not translated yet") == "Not translated yet"
        assert i18n.format_date(date(2026, 10, 16), "EEE d. MMM") == "fre. 16. okt."
    finally:
        i18n.set_locale("en", "Europe/London")
        i18n._catalogue.cache_clear()
    assert i18n._("due") == "due"
