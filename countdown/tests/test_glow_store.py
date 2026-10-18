"""The energy panel from the store (`v1:energy:<device id>`, written by auth-broker's glow-provider).

fixtures/glow_energy.json is the contract with auth-broker, whose tests read the same file: what Glowmarkt answers,
what's asked of it, and the stored value the broker writes from those answers. The panel drawn from the stored value
must be the one the renderer drew from Glowmarkt's answers when it asked Glowmarkt itself: fixtures/
glow_energy_pages.json is what it drew then, from the same answers at the fixture's `now`.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import responses
from test_utils import REGISTRATION

from countdown_api.models import Glowmarkt
from countdown_core.core.abstract_client import ClientStatus
from countdown_core.glow import glow_client
from countdown_core.glow.glow_client import GlowClient, NoStoredEnergyError
from countdown_core.store import keys
from countdown_core.store.energy import StoredEnergy
from countdown_credentials.registration import RendererRegistration

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "glow_energy.json").read_text())
STORED = FIXTURE["stored"]
DEVICE_ID = FIXTURE["device_id"]
# The pages the renderer drew from FIXTURE's Glowmarkt answers when it signed in to Glowmarkt itself.
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

    monkeypatch.setattr(glow_client, "datetime", FrozenDatetime)
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


def make_client(registration=None) -> GlowClient:
    registration = registration or RendererRegistration(REGISTRATION.broker_url, REGISTRATION.device_secret, DEVICE_ID)
    return GlowClient(registration, Glowmarkt(enabled=True))


def draw_pages(client: GlowClient) -> list[list[dict]]:
    asyncio.run(client.initialise())
    pages = []
    for page_index in PAGES:
        client.page_index = page_index
        pages.append(asyncio.run(client.update()).readings)
    return pages


def test_the_fixtures_stored_value_is_a_stored_energy():
    stored = StoredEnergy.model_validate(STORED)

    assert stored.fetched_at == datetime(2026, 9, 25, 5, 10, tzinfo=UTC)
    assert stored.zone().utcoffset(None) == timedelta(hours=1)
    assert [len(stored.readings(p).data) for p in ("PT1H", "P1D", "P1M")] == [25, 32, 9]


def test_the_panel_from_the_store_is_the_panel_the_renderer_drew_from_glowmarkt(clock, store, no_http):
    from_store = draw_pages(make_client())

    assert from_store == GLOWMARKT_PAGES
    assert [len(page) for page in from_store] == [25, 32, 9]
    assert from_store[0][0] == {"start": "2026-09-24T06:00:00+01:00", "kwh": 0.21}
    assert from_store[2][-1]["start"] == "2026-09-01T00:00:00+01:00"


def test_the_store_is_read_without_any_http(clock, store, no_http):
    client = make_client()

    pages = draw_pages(client)

    assert [len(page) for page in pages] == [25, 32, 9]
    assert client.status == ClientStatus.CONNECTED
    assert len(no_http.calls) == 0


def test_a_page_drawn_later_is_cut_to_its_window_as_of_then(clock, store, no_http):
    """An hour after the fetch, the last 24 hours start an hour later; the 31 days and the year are the same."""
    clock.now += timedelta(hours=1)

    pages = draw_pages(make_client())

    assert pages[0][0]["start"] == "2026-09-24T07:00:00+01:00"
    assert [len(page) for page in pages] == [24, 32, 9]


def test_the_readings_are_drawn_in_the_zone_they_were_asked_for_in(clock, store, no_http, monkeypatch):
    """A renderer whose machine is on UTC still labels the buckets by the screen's time, as the broker asked."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    clock.now = clock.now.astimezone(UTC)

    pages = draw_pages(make_client())

    assert pages[1][0]["start"] == "2026-08-25T00:00:00+01:00"


def test_no_readings_in_the_store_raises_so_the_last_panel_is_kept(clock, store, no_http):
    store.delete(keys.energy(DEVICE_ID))
    client = make_client()
    asyncio.run(client.initialise())

    with pytest.raises(NoStoredEnergyError) as raised:
        asyncio.run(client.update())

    assert isinstance(raised.value, LookupError)
    assert client.page_index == 0
    assert len(no_http.calls) == 0
