"""The energy panel from the store (`v1:energy:<device id>`, written by auth-broker's glow-provider), and only then.

fixtures/glow_energy.json is the contract with auth-broker, whose tests read the same file: what Glowmarkt answers,
what's asked of it, and the stored value the broker writes from those answers. The panel drawn from the stored value
must be the one drawn from Glowmarkt's answers directly.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fakeredis
import pytest
import responses
from test_utils import REGISTRATION

from countdown_api.models import Glowmarkt
from countdown_core.core.abstract_client import ClientStatus
from countdown_core.glow import glow_client
from countdown_core.glow.glow_client import GlowClient, NoStoredEnergyError
from countdown_core.store import keys, reader
from countdown_core.store.energy import StoredEnergy
from countdown_credentials.registration import Registration, RendererRegistration

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "glow_energy.json").read_text())
STORED = FIXTURE["stored"]
DEVICE_ID = FIXTURE["device_id"]
BASE_URL = GlowClient.base_url
READINGS_URL = f"{BASE_URL}/resource/{STORED['resource_id']}/readings"
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
def glowmarkt():
    """Glowmarkt answering as in the fixture; a readings request other than the fixture's fails the test."""

    def readings(request):
        period = request.params["period"]
        assert request.params == FIXTURE["requests"][period]
        return 200, {}, json.dumps(FIXTURE["readings"][period])

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.POST, f"{BASE_URL}/auth", json=FIXTURE["auth"])
        rsps.add(responses.GET, f"{BASE_URL}/virtualentity", json=FIXTURE["virtualentity"])
        rsps.add_callback(responses.GET, READINGS_URL, callback=readings)
        yield rsps


@pytest.fixture
def no_http():
    """Fail any HTTP request at all: nothing is registered."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def store(monkeypatch):
    """Return a fake Redis that this renderer reads, as if it had been given REDIS_URL, holding the fixture's value."""
    client = fakeredis.FakeRedis(decode_responses=True)
    client.set(keys.energy(DEVICE_ID), json.dumps(STORED))
    monkeypatch.setattr(reader, "store", lambda: client)
    return client


@pytest.fixture
def no_store(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)


def make_client(registration=None) -> GlowClient:
    registration = registration or RendererRegistration(REGISTRATION.broker_url, REGISTRATION.device_secret, DEVICE_ID)
    return GlowClient(registration, Glowmarkt(username="me@example.com", password="hunter2"))


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


def test_a_renderer_without_the_store_asks_glowmarkt_what_the_broker_asks(clock, glowmarkt, no_store):
    """The glowmarkt fixture checks each readings request is the fixture's, which auth-broker's tests check too."""
    pages = draw_pages(make_client())

    assert [len(page) for page in pages] == [25, 32, 9]
    assert len([c for c in glowmarkt.calls if "/readings" in c.request.url]) == 3


def test_the_panel_from_the_store_is_the_panel_from_glowmarkt(clock, glowmarkt, monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    direct = draw_pages(make_client())
    stored_client = fakeredis.FakeRedis(decode_responses=True)
    stored_client.set(keys.energy(DEVICE_ID), json.dumps(STORED))
    monkeypatch.setattr(reader, "store", lambda: stored_client)
    calls_before = len(glowmarkt.calls)

    from_store = draw_pages(make_client())

    assert from_store == direct
    assert len(glowmarkt.calls) == calls_before
    assert from_store[0][0] == {"start": "2026-09-24T06:00:00+01:00", "kwh": 0.21}
    assert from_store[2][-1]["start"] == "2026-09-01T00:00:00+01:00"


def test_the_store_is_read_without_any_http(clock, store, no_http):
    client = make_client()

    pages = draw_pages(client)

    assert [len(page) for page in pages] == [25, 32, 9]
    assert client.status == ClientStatus.CONNECTED
    assert client.token is None
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


def test_without_the_store_it_signs_in_to_glowmarkt_as_before(clock, glowmarkt, no_store):
    client = make_client()
    asyncio.run(client.initialise())

    assert client.token == FIXTURE["auth"]["token"]
    assert client.resource_id == STORED["resource_id"]


def test_without_a_device_id_it_asks_glowmarkt_even_with_a_store(clock, glowmarkt, store):
    client = make_client(Registration(REGISTRATION.broker_url, REGISTRATION.device_secret))
    asyncio.run(client.initialise())

    assert client.token == FIXTURE["auth"]["token"]
