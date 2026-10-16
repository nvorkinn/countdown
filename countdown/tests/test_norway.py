"""A Norwegian screen: its postcode, holidays, weather warnings, Entur disruptions, time zone and language."""

from datetime import UTC, date, datetime

import pytest
import requests
import responses
from test_utils import REGISTRATION

from countdown_api.models import DepartureStop
from countdown_core import i18n
from countdown_core.departures.departures_client import DeparturesSettings
from countdown_core.entur.entur_client import JOURNEY_PLANNER_URL
from countdown_core.notices.calendar import ClockChangeSource
from countdown_core.notices.entur import EnturSituationsSource
from countdown_core.notices.norway import (
    ADDRESSES_URL,
    METALERTS_URL,
    MetAlertsSource,
    NorwegianHolidaySource,
    lookup_norwegian_postcode,
    norwegian_holidays,
    parse_metalerts,
)
from countdown_core.notices.notice import Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.tfl.models import Postcode

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
        "Orange warning: kraftig vind until Fri 21:00",
    )
    assert (yellow.severity, yellow.text) == (Severity.INFO, "Yellow warning: regn until Sat 08:00")


def test_metalerts_are_asked_for_in_the_screens_language(api):
    api.get(METALERTS_URL, json={"features": []})
    i18n.set_locale("nb", "Europe/Oslo")
    try:
        MetAlertsSource(requests.Session(), OSLO).fetch(NOW)
    finally:
        i18n.set_locale("en", "Europe/London")
    request = api.calls[0].request
    assert "lang=no" in request.url and "lat=59.9116" in request.url
    assert request.headers["User-Agent"].startswith("countdown")


def test_entur_situations_are_said_once_tagged_with_their_line_in_the_screens_language(api):
    situation = {
        "summary": [{"value": "Buss for T-bane", "language": "no"}, {"value": "Bus for metro", "language": "en"}],
        "validityPeriod": {"endTime": "2026-10-20T00:00:00+00:00"},
        "severity": "severe",
    }
    calls = [{"serviceJourney": {"line": {"publicCode": "5"}}, "situations": [situation]}] * 3
    api.post(
        JOURNEY_PLANNER_URL,
        json={"data": {"stopPlace": {"name": "Majorstuen", "situations": [], "estimatedCalls": calls}}},
    )

    [notice] = EnturSituationsSource(requests.Session(), ["NSR:StopPlace:58381"]).fetch(NOW)

    assert (notice.source, notice.severity, notice.text) == ("Line 5", Severity.WARNING, "Bus for metro")


def test_the_clocks_change_in_the_screens_time_zone(oslo_screen):
    [notice] = ClockChangeSource(requests.Session()).fetch(datetime(2026, 10, 24, 18, tzinfo=UTC))
    # 01:00 UTC is 3am in Oslo, before the clocks go back to 2am.
    assert notice.text == "Clocks go back an hour tonight, at 3am"


def board(postcode: str | None, country: str | None, stops: tuple[DepartureStop, ...] = ()) -> NoticeBoardClient:
    return NoticeBoardClient(REGISTRATION, NoticeBoardSettings(postcode, DeparturesSettings(stops), country))


def test_a_norwegian_postcode_brings_norways_sources(api):
    api.get(
        ADDRESSES_URL,
        json={
            "adresser": [
                {"postnummer": "0150", "kommunenavn": "OSLO", "representasjonspunkt": {"lat": 59.9, "lon": 10.7}}
            ]
        },
    )
    client = board("0150", "NO", (DepartureStop(source="entur", id="NSR:Quay:7333"),))
    client._ensure_location(NOW)
    names = [type(source).__name__ for source in client.sources]
    assert names == ["ClockChangeSource", "EnturSituationsSource", "NorwegianHolidaySource", "MetAlertsSource"]


def test_a_screen_on_oslo_time_without_a_postcode_gets_norways_holidays(oslo_screen):
    assert isinstance(board(None, None).sources[-1], NorwegianHolidaySource)


def test_text_is_drawn_in_the_screens_language(tmp_path, monkeypatch):
    (tmp_path / "nb.po").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=utf-8\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
        'msgid "due"\nmsgstr "nå"\n\nmsgid "{n} min"\nmsgid_plural "{n} mins"\nmsgstr[0] "{n} min"\nmsgstr[1] "{n} min"\n\n'
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
