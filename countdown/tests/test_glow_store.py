"""The energy panel from the store (`v1:energy:<device id>`, written by auth-broker's glow-provider).

The store contract's glow_energy.json (countdown_api.fixtures), which auth-broker's tests read too: what Glowmarkt
answers, what's asked of it, and the stored value the broker writes from those answers. The panel drawn from the
stored value must be the one in fixtures/glow_energy_pages.json: what the client drew from the same Glowmarkt answers,
at the fixture's `now`, when it asked Glowmarkt itself.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import responses

from countdown_api import fixtures
from countdown_api.models import Glowmarkt
from countdown_core.core import moment
from countdown_core.core.abstract_client import ClientStatus
from countdown_core.glow.glow_client import GlowClient, NoStoredEnergyError
from countdown_core.store import keys, reader
from countdown_core.store.energy import StoredEnergy

FIXTURE = fixtures.load("glow_energy.json")
STORED = FIXTURE["stored"]
DEVICE_ID = FIXTURE["device_id"]
# The pages drawn from FIXTURE's Glowmarkt answers when the client signed in to Glowmarkt itself.
GLOWMARKT_PAGES = json.loads((Path(__file__).parent / "fixtures" / "glow_energy_pages.json").read_text())
PAGES = (0, 1, 2)


@pytest.fixture
def clock(monkeypatch):
    """Freeze GlowClient's now at the fixture's, in UK time (the fixture's zone), as the machine's zone too."""
    monkeypatch.setenv("TZ", FIXTURE["zone"])
    time.tzset()

    class Clock:
        now = datetime.fromisoformat(FIXTURE["now"])

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return Clock.now if tz is None else Clock.now.astimezone(tz)

    # The moment the board draws at (core.moment), as a render sets it.
    monkeypatch.setattr(moment, "now", lambda: FrozenDatetime.now(UTC))
    yield Clock
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def no_http():
    """Fail any HTTP request at all: nothing is registered."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def store(store):
    """Return the store this renderer reads, holding the fixture's value."""
    store.set(keys.energy(DEVICE_ID), json.dumps(STORED))
    return store


def make_client() -> GlowClient:
    return GlowClient(DEVICE_ID, Glowmarkt(enabled=True))


def draw_pages(client: GlowClient, rotation_clock) -> list[list[dict]]:
    """Draw each page in turn, a minute apart, as the clock turns them (core.rotation)."""
    asyncio.run(client.initialise())
    pages = []
    for page_index in PAGES:
        rotation_clock.at_minute(page_index)
        pages.append(asyncio.run(client.update()).readings)
    return pages


def test_the_fixtures_stored_value_is_a_stored_energy():
    stored = StoredEnergy.model_validate(STORED)

    assert stored.fetched_at == datetime(2026, 9, 25, 5, 10, tzinfo=UTC)
    assert stored.zone().utcoffset(None) == timedelta(hours=1)
    assert [len(stored.readings(p).data) for p in ("PT1H", "P1D", "P1M")] == [25, 32, 9]


def test_the_panel_from_the_store_is_the_panel_the_renderer_drew_from_glowmarkt(clock, store, no_http, rotation_clock):
    from_store = draw_pages(make_client(), rotation_clock)

    assert from_store == GLOWMARKT_PAGES
    assert [len(page) for page in from_store] == [25, 32, 9]
    assert from_store[0][0] == {"start": "2026-09-24T06:00:00+01:00", "kwh": 0.21}
    assert from_store[2][-1]["start"] == "2026-09-01T00:00:00+01:00"


def test_the_store_is_read_without_any_http(clock, store, no_http, rotation_clock):
    client = make_client()

    pages = draw_pages(client, rotation_clock)

    assert [len(page) for page in pages] == [25, 32, 9]
    assert client.status == ClientStatus.CONNECTED
    assert len(no_http.calls) == 0


def test_a_page_drawn_later_is_cut_to_its_window_as_of_then(clock, store, no_http, rotation_clock):
    """An hour after the fetch, the last 24 hours start an hour later; the 31 days and the year are the same."""
    clock.now += timedelta(hours=1)

    pages = draw_pages(make_client(), rotation_clock)

    assert pages[0][0]["start"] == "2026-09-24T07:00:00+01:00"
    assert [len(page) for page in pages] == [24, 32, 9]


def test_the_readings_are_drawn_in_the_zone_they_were_asked_for_in(clock, store, no_http, monkeypatch, rotation_clock):
    """A renderer whose machine is on UTC still labels the buckets by the screen's time, as the broker asked."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    clock.now = clock.now.astimezone(UTC)

    pages = draw_pages(make_client(), rotation_clock)

    assert pages[1][0]["start"] == "2026-08-25T00:00:00+01:00"


def test_no_readings_in_the_store_say_the_energy_is_not_available(clock, store, no_http):
    """The key has expired (glow-provider is down): no readings older than its TTL are drawn."""
    store.delete(keys.energy(DEVICE_ID))
    client = make_client()
    asyncio.run(client.initialise())

    with pytest.raises(NoStoredEnergyError) as raised:
        client._update()
    panel = asyncio.run(client.update())

    assert isinstance(raised.value, reader.NoStoredDataError)
    assert (panel.title, panel.message) == ("Energy", "Not available")
    assert len(no_http.calls) == 0
