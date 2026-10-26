"""The notice board's flood warnings from the store (`v1:notices:floods:<place>`, written by the floods provider).

fixtures/floods.json is one cycle of the provider: the EA's and postcodes.io's answers, and the value it stores for
each postcode. The notices the board makes from a stored value must be those in fixtures/floods_notices.json, in
each language (recorded from the board when it asked the EA itself, from the same warnings).
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests
import responses
from test_utils import TEST_DEVICE_ID

from countdown_renderer import moment
from countdown_renderer.notices.floods import NoStoredFloodsError, StoredFloodWarningsSource
from countdown_renderer.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_shared import i18n
from countdown_shared.store import keys, reader
from countdown_shared.store.floods import StoredFloods

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "floods.json").read_text())
# What the board shows for each postcode, in each language.
EA_NOTICES = json.loads((FIXTURES / "floods_notices.json").read_text())
POSTCODES = list(FIXTURE["places"])
NOW = datetime.fromisoformat(FIXTURE["fetched_at"])


def stored(postcode: str) -> dict:
    return FIXTURE["places"][postcode]["stored"]


def as_json(notice) -> dict:
    return {
        "source": notice.source,
        "severity": notice.severity.name,
        "text": notice.text,
        "valid_until": notice.valid_until and notice.valid_until.isoformat(),
        "display_time": notice.display_time and notice.display_time.isoformat(),
    }


@pytest.fixture
def no_http():
    """Fail any HTTP request at all: nothing is registered."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def language():
    """Set the screen's language, then put English back."""
    yield lambda lang: i18n.set_locale(lang, "Europe/London")
    i18n.set_locale("en", "Europe/London")


def test_the_fixtures_stored_values_are_stored_floods():
    for postcode in POSTCODES:
        value = StoredFloods.model_validate(stored(postcode))

        assert value.postcode == postcode
        assert value.fetched_at == NOW
    assert len(StoredFloods.model_validate(stored("SE17 3LL")).floods.items) == 4  # one no longer in force


@pytest.mark.parametrize("lang", ["en", "nb"])
@pytest.mark.parametrize("postcode", POSTCODES)
def test_the_notices_from_the_store_are_the_ones_the_board_made_from_the_ea(store, no_http, language, postcode, lang):
    store.set(keys.floods(postcode), json.dumps(stored(postcode)))
    language(lang)

    notices = StoredFloodWarningsSource(requests.Session(), postcode).fetch(NOW)

    assert [as_json(notice) for notice in notices] == EA_NOTICES[postcode][lang]
    assert len(no_http.calls) == 0


def test_the_language_is_worked_out_as_the_notices_are_read(store, language):
    """The store holds the EA's words, not the board's: the same value reads in whatever language the screen is in."""
    store.set(keys.floods("YO1 7HH"), json.dumps(stored("YO1 7HH")))
    source = StoredFloodWarningsSource(requests.Session(), "YO1 7HH")

    english = source.fetch(NOW)
    language("nb")
    norwegian = source.fetch(NOW)

    assert [n.text for n in english] == [
        "Flood warning: River Ouse at York city centre",
        "Flood alert: River Foss in York",
    ]
    assert [n.text for n in norwegian] == ["Flomfare: River Ouse at York city centre", "Flomvarsel: River Foss in York"]
    assert {n.source for n in norwegian} == {"Flom"}


def test_a_place_with_nothing_in_force_is_all_clear_not_missing(store):
    store.set(keys.floods("CF10 1EP"), json.dumps(stored("CF10 1EP")))

    assert StoredFloodWarningsSource(requests.Session(), "CF10 1EP").fetch(NOW) == []


def test_the_key_is_the_postcodes_place_however_the_postcode_is_written(store):
    store.set("v1:notices:floods:SE173LL", json.dumps(stored("SE17 3LL")))

    assert len(StoredFloodWarningsSource(requests.Session(), "se17 3ll").fetch(NOW)) == 3


def test_no_warnings_in_the_store_raises_without_naming_the_postcode(store, no_http):
    with pytest.raises(NoStoredFloodsError) as raised:
        StoredFloodWarningsSource(requests.Session(), "SE17 3LL").fetch(NOW)

    assert isinstance(raised.value, reader.NoStoredDataError)
    assert "SE17" not in str(raised.value)
    assert len(no_http.calls) == 0


def test_a_renderer_without_a_store_says_so(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)

    with pytest.raises(reader.StoreNotConfiguredError):
        StoredFloodWarningsSource(requests.Session(), "SE17 3LL").fetch(NOW)


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


def test_the_board_drops_the_flood_notices_once_the_key_has_expired(store, clock, no_http):
    store.set(keys.floods("SE17 3LL"), json.dumps(stored("SE17 3LL")))
    board = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(postcode=None))
    board.sources = [StoredFloodWarningsSource(requests.Session(), "SE17 3LL")]
    first = asyncio.run(board.update()).notices

    store.delete(keys.floods("SE17 3LL"))  # the provider's down, and its key has expired
    clock.now += timedelta(minutes=1)
    expired = asyncio.run(board.update())

    assert [n.text for n in first] == [
        "Severe flood warning: Tidal Thames at Bankside and Rotherhithe",
        "Flood warning: River Thames at Southwark",
        "Flood alert: Lower River Effra",
    ]
    assert expired.message == "Not available"  # its only source: no old warnings, and no "All clear" either
