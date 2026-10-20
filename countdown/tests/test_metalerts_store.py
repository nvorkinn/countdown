"""MET Norway's weather warnings on the notice board, read from the store (`v1:notices:metalerts`, fakeredis).

fixtures/metalerts_current.json is the stored value: MetAlerts' current.json for all of Norway, in Norwegian and in
English, as the provider writes it (test_metalerts_provider.py checks that side). The board picks out the warnings
covering its place, which must be exactly the Notices in fixtures/metalerts_notices.json
(recorded when it asked MET for the place itself).
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests
import responses
from test_utils import TEST_DEVICE_ID

from countdown_core import i18n
from countdown_core.core import moment
from countdown_core.notices.norway import (
    NoStoredMetAlertsError,
    StoredMetAlertsSource,
    covers,
    warnings_at,
)
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.store import keys, reader
from countdown_core.store.metalerts import StoredMetAlerts
from countdown_core.tfl.models import Postcode
from countdown_core.utils.clock import format_clock

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "metalerts_current.json").read_text(encoding="utf-8"))
RECORDED = json.loads((FIXTURES / "metalerts_notices.json").read_text(encoding="utf-8"))["cases"]
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

OSLO = Postcode(postcode="0150", latitude=59.9116, longitude=10.7505, country="Norway", admin_district="Oslo")
TROMSO = Postcode(postcode="9008", latitude=69.6489, longitude=18.9551, country="Norway", admin_district="Tromsø")
DRAMMEN = Postcode(postcode="3015", latitude=59.7439, longitude=10.2045, country="Norway", admin_district="Drammen")
HAMMERFEST = Postcode(postcode="9600", latitude=70.6634, longitude=23.6821, country="Norway")
PLACES = {place.postcode: place for place in (OSLO, TROMSO, DRAMMEN, HAMMERFEST)}
# What MET answers each place (current.json?lat=...&lon=...): every warning whose area covers it, by id. Oslo's are
# the wind (an Oslo and Akershus polygon), the rain (the Oslo half of a MultiPolygon) and yesterday's ice; not the
# snow, whose polygon has Oslo as a hole, nor the gale out in the Skagerrak. Drammen is under the snow only.
COVERING = {
    "0150": {
        "2.49.0.1.578.0.20261016050512.001",
        "2.49.0.1.578.0.20261016051033.002",
        "2.49.0.1.578.0.20261014181500.003",
    },
    "9008": {"2.49.0.1.578.0.20261016070000.005"},
    "3015": {"2.49.0.1.578.0.20261016052245.004"},
    "9600": set(),
}


def stored_path(store, place: Postcode, now: datetime):
    """Make the board's notices from the warnings the provider stored, for all of Norway."""
    store.set(keys.metalerts(), StoredMetAlerts.model_validate(FIXTURE).model_dump_json())
    return StoredMetAlertsSource(requests.Session(), place).fetch(now)


def recorded(case: dict) -> list[Notice]:
    return [
        Notice(
            notice["source"],
            Severity[notice["severity"]],
            notice["text"],
            datetime.fromisoformat(notice["valid_until"]) if notice["valid_until"] else None,
            datetime.fromisoformat(notice["display_time"]) if notice["display_time"] else None,
        )
        for notice in case["notices"]
    ]


@pytest.mark.parametrize(
    "case", RECORDED, ids=lambda case: f"{case['postcode']}-{case['language']}-{case['now'][11:16]}"
)
def test_the_stored_warnings_make_the_notices_the_board_made_when_it_asked_met(store, case):
    i18n.set_locale(case["language"], "Europe/Oslo")
    try:
        made = stored_path(store, PLACES[case["postcode"]], datetime.fromisoformat(case["now"]))
    finally:
        i18n.set_locale("en", "Europe/London")
    assert made == recorded(case)


@pytest.mark.parametrize("postcode", sorted(COVERING))
def test_a_place_gets_the_warnings_met_would_answer_it_with(postcode):
    for lang in ("no", "en"):
        ids = {feature["properties"]["id"] for feature in warnings_at(FIXTURE[lang], PLACES[postcode])["features"]}
        assert ids == COVERING[postcode]


def test_oslo_is_warned_of_the_wind_and_the_rain_in_english(store):
    wind, rain = stored_path(store, OSLO, NOW)
    assert (wind.source, wind.severity, wind.text) == ("MET", Severity.WARNING, "Orange warning: wind until Fri")
    assert format_clock(wind.display_time) == "21:00"  # in Oslo's time, 19:00 UTC
    assert wind.valid_until == datetime(2026, 10, 16, 19, tzinfo=UTC)
    # Yellow from its awareness_level alone: this one has no riskMatrixColor.
    assert (rain.severity, rain.text) == (Severity.INFO, "Yellow warning: rain until Sat")


def test_a_norwegian_screen_reads_mets_norwegian_answer(store):
    i18n.set_locale("nb", "Europe/Oslo")
    try:
        [surge] = stored_path(store, TROMSO, NOW)
    finally:
        i18n.set_locale("en", "Europe/London")
    assert (surge.severity, surge.text) == (Severity.SEVERE, "Rødt farevarsel: stormflo til fre.")


def test_warnings_are_worded_when_read_so_a_language_change_needs_nothing_stored(store):
    """The store holds MET's answers, not text: the same value gives each screen its own language."""
    english = stored_path(store, DRAMMEN, NOW)
    i18n.set_locale("nb", "Europe/Oslo")
    try:
        norwegian = StoredMetAlertsSource(requests.Session(), DRAMMEN).fetch(NOW)
    finally:
        i18n.set_locale("en", "Europe/London")
    assert [notice.text for notice in english] == ["Yellow warning: snow until Sat"]
    assert [notice.text for notice in norwegian] == ["Gult farevarsel: snø til lør."]


def test_no_warnings_in_the_store_raises_a_no_stored_data_error_so_the_board_leaves_them_out(store):
    with pytest.raises(NoStoredMetAlertsError) as raised:
        StoredMetAlertsSource(requests.Session(), OSLO).fetch(NOW)
    assert isinstance(raised.value, reader.NoStoredDataError)


def test_the_board_drops_the_warnings_once_the_key_has_expired(store, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    # The moment the board draws at (core.moment), as a render sets it.
    monkeypatch.setattr(moment, "now", lambda: FrozenDatetime.now(UTC))
    board = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(None, "NO"))
    source = StoredMetAlertsSource(board.session, OSLO)
    board.sources = [source]
    warnings = stored_path(store, OSLO, NOW)
    first = asyncio.run(board.update())

    store.delete(keys.metalerts())  # the provider's down, and its key has expired

    assert sorted(first.notices, key=lambda n: n.text) == sorted(warnings, key=lambda n: n.text)
    assert asyncio.run(board.update()).message == "Not available"  # its only source


def test_a_renderer_without_a_store_says_so(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    with pytest.raises(reader.StoreNotConfiguredError):
        StoredMetAlertsSource(requests.Session(), OSLO).fetch(NOW)


def test_the_stored_source_asks_met_nothing(store):
    with responses.RequestsMock():  # any request would fail the test
        assert len(stored_path(store, OSLO, NOW)) == 2


SQUARE = [[10.0, 59.0], [11.0, 59.0], [11.0, 60.0], [10.0, 60.0], [10.0, 59.0]]
HOLE = [[10.4, 59.4], [10.6, 59.4], [10.6, 59.6], [10.4, 59.6], [10.4, 59.4]]


@pytest.mark.parametrize(
    ("geometry", "latitude", "longitude", "expected"),
    [
        ({"type": "Polygon", "coordinates": [SQUARE]}, 59.2, 10.2, True),
        ({"type": "Polygon", "coordinates": [SQUARE]}, 60.2, 10.2, False),
        # GeoJSON is longitude first: this point is only inside if the order were mixed up.
        (
            {"type": "Polygon", "coordinates": [[[59.0, 10.0], [60.0, 10.0], [60.0, 11.0], [59.0, 11.0]]]},
            59.5,
            10.5,
            False,
        ),
        ({"type": "Polygon", "coordinates": [SQUARE, HOLE]}, 59.5, 10.5, False),
        ({"type": "Polygon", "coordinates": [SQUARE, HOLE]}, 59.2, 10.2, True),
        # An unclosed ring is closed for it.
        ({"type": "Polygon", "coordinates": [SQUARE[:-1]]}, 59.2, 10.2, True),
        ({"type": "MultiPolygon", "coordinates": [[HOLE], [SQUARE]]}, 59.2, 10.2, True),
        ({"type": "MultiPolygon", "coordinates": [[HOLE]]}, 59.2, 10.2, False),
        # A third, altitude, coordinate is ignored.
        ({"type": "Polygon", "coordinates": [[[*point, 0.0] for point in SQUARE]]}, 59.2, 10.2, True),
        ({"type": "Point", "coordinates": [10.2, 59.2]}, 59.2, 10.2, False),
        (None, 59.2, 10.2, False),
        ({"type": "Polygon", "coordinates": [[]]}, 59.2, 10.2, False),
    ],
)
def test_a_warning_covers_the_points_inside_its_area(geometry, latitude, longitude, expected):
    assert covers(geometry, latitude, longitude) is expected
