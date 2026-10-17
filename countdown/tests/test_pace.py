"""The refresh pace: a provider asks for a faster refresh, and it reaches the loop, the target and the broker.

Provider -> store (`v1:pace:...`, with a TTL) -> ApiRegistry.pace() -> DisplayLoop -> DisplayController -> ServerTarget
-> `X-Refresh-Hint` on the frame upload. The providers' side is in test_provider_runner.py.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest
import responses
from config_factory import make_config
from fake_target import FakeTarget
from PIL import Image
from test_display_controller import frame, show
from test_utils import REGISTRATION

from countdown_core.core.abstract_client import AbstractClient
from countdown_core.core.api_registry import API_NAMES, ApiRegistry
from countdown_core.core.display import DisplayController
from countdown_core.core.display_loop import MIN_PACE_S, DisplayLoop
from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_core.store import keys, reader
from countdown_core.store.developer import StoredDeveloper
from countdown_credentials.registration import RendererRegistration
from countdown_server.server_target import REFRESH_HINT_HEADER, ServerTarget

FIXTURE = Path(__file__).parent / "fixtures" / "developer.json"
BROKER_URL = "https://broker.example.com"
FRAME_URL = f"{BROKER_URL}/api/frame"
PACE_KEY = keys.pace(keys.DEVELOPER, "device-123")


@pytest.fixture
def store(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: client)
    return client


def developer_client() -> DeveloperClient:
    return DeveloperClient(REGISTRATION, DeveloperSettings(enabled=True, style="card"))


# --- the keys and the reader -------------------------------------------------------------------------------------


def test_the_pace_key_is_per_source_and_item():
    assert keys.pace("developer", "abc") == "v1:pace:developer:abc"
    assert keys.developer("abc") == "v1:developer:abc"
    assert keys.DEVELOPER in keys.SOURCES


def test_the_fastest_pace_is_the_shortest_asked_for(store):
    store.set("a", "45")
    store.set("b", "20")

    assert reader.fastest_pace(store, ["a", "b", "missing"]) == 20


@pytest.mark.parametrize("junk", ["", "soon", "-5", "0", "2.5"])
def test_junk_is_no_pace(store, junk):
    store.set("a", junk)

    assert reader.fastest_pace(store, ["a"]) is None


def test_no_keys_is_no_pace(store):
    assert reader.fastest_pace(store, []) is None


# --- the developer provider's data ------------------------------------------------------------------------------


def test_the_shared_fixture_is_a_stored_developer():
    stored = StoredDeveloper.model_validate_json(FIXTURE.read_text())

    assert stored.github.login == "nvorkinn"
    assert [release.state for release in stored.releases] == ["running"]
    assert [plan.name for plan in stored.plans] == ["Copilot", "Claude"]


def test_the_developer_client_watches_its_devices_pace_key(store):
    assert developer_client().pace_keys() == [PACE_KEY]


def test_clients_ask_for_no_pace_unless_they_say(store):
    assert AbstractClient.pace_keys(developer_client()) == []


# --- the registry ---------------------------------------------------------------------------------------------


def registry_with_developer() -> ApiRegistry:
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    registry.clients["developer"] = developer_client()
    return registry


def test_the_registry_reports_the_pace_a_provider_asks_for(store):
    store.set(PACE_KEY, "20", ex=60)

    assert asyncio.run(registry_with_developer().pace()) == 20


def test_the_registry_reports_none_once_the_pace_lapses(store):
    assert asyncio.run(registry_with_developer().pace()) is None


def test_the_registry_reports_none_without_a_store(store, monkeypatch):
    registry = registry_with_developer()
    monkeypatch.setattr(reader, "store", lambda: None)

    assert asyncio.run(registry.pace()) is None


def test_a_store_that_cant_be_read_is_no_pace(monkeypatch, store):
    def unreachable(*_):
        raise reader.redis.exceptions.ConnectionError

    monkeypatch.setattr(reader, "fastest_pace", unreachable)

    assert asyncio.run(registry_with_developer().pace()) is None


# --- the loop's sleep --------------------------------------------------------------------------------------------


def loop_at(interval: int, pace: int | None) -> DisplayLoop:
    config = make_config()
    config.interval = interval
    loop = DisplayLoop(SimpleNamespace(registration=REGISTRATION), config, None, DisplayController(FakeTarget()))
    loop._pace = pace  # what the last cycle read
    return loop


@pytest.mark.parametrize(
    ("interval", "pace", "sleeps"),
    [
        (60, None, 60),  # nothing asks: the usual
        (60, 20, 20),  # a release: faster
        (60, 45, 45),
        (60, 5, MIN_PACE_S),  # never faster than the floor
        (60, 300, 60),  # never slower than the usual
        (0, 20, 0),  # an interval of 0 still ends the loop after the cycle
    ],
)
def test_the_loop_sleeps_the_shorter_of_the_interval_and_the_pace(interval, pace, sleeps):
    assert loop_at(interval, pace).sleep_s() == sleeps


# --- the controller and the targets --------------------------------------------------------------------------


def test_the_controller_passes_the_pace_to_its_target():
    target = FakeTarget()
    DisplayController(target).set_pace(20)

    assert target.paces == [20]


def test_an_unchanged_picture_is_not_repainted_but_renews_the_pace():
    target = FakeTarget()
    controller = DisplayController(target)

    show(controller, frame())
    show(controller, frame())

    assert (len(target.paints), target.renewals) == (1, 1)


def server_target() -> ServerTarget:
    return ServerTarget(RendererRegistration(BROKER_URL, "device-secret", "device-123"))


def image() -> Image.Image:
    return Image.new("1", (800, 480), 1)


@responses.activate
def test_a_frame_without_a_pace_carries_no_hint():
    responses.put(FRAME_URL)

    server_target().paint(image())

    assert REFRESH_HINT_HEADER not in responses.calls[0].request.headers


@responses.activate
def test_a_frame_carries_the_pace_the_providers_ask_for():
    responses.put(FRAME_URL)
    target = server_target()
    target.set_pace(20)

    target.paint(image())

    assert responses.calls[0].request.headers[REFRESH_HINT_HEADER] == "20"


@responses.activate
def test_renewing_sends_the_last_frame_again_with_the_pace():
    responses.put(FRAME_URL)
    target = server_target()
    target.set_pace(20)
    target.paint(image())

    target.renew()

    first, again = (call.request for call in responses.calls)
    assert again.body == first.body
    assert again.headers[REFRESH_HINT_HEADER] == "20"


@responses.activate
def test_renewing_does_nothing_when_no_pace_is_asked_for():
    responses.put(FRAME_URL)
    target = server_target()
    target.paint(image())

    target.renew()

    assert len(responses.calls) == 1


@responses.activate
def test_renewing_after_the_pace_ends_tells_the_broker_once():
    responses.put(FRAME_URL)
    target = server_target()
    target.set_pace(20)
    target.paint(image())
    target.set_pace(None)

    target.renew()
    target.renew()

    assert len(responses.calls) == 2
    assert REFRESH_HINT_HEADER not in responses.calls[1].request.headers


def test_renewing_before_any_frame_does_nothing():
    target = server_target()
    target.set_pace(20)

    target.renew()  # no mocked HTTP: sending would fail
