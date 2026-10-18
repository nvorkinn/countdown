"""The screen's config from the store: auth-broker's copy of GET /api/config, its nudge, and the broker as fallback.

auth-broker writes `v1:config:<device id>` from its database and publishes the device id on `v1:config:changed`;
the renderer reads it (BrokerClient.get_config), asks the broker only when it isn't there, and redraws as soon as it's
nudged (ConfigWatcher, DisplayLoop calling the render at once). tests/fixtures/config_store.json is the stored shape,
the same file as auth-broker's.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import responses
from config_factory import broker_payload
from fake_target import FakeTarget

from countdown_api.models import ConfigResponse
from countdown_core.config_server import broker_client
from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.core import display_loop
from countdown_core.core.display import DisplayController
from countdown_core.core.display_loop import DisplayLoop
from countdown_core.store import config as stored_config
from countdown_core.store import keys, reader
from countdown_core.store.config import ConfigWatcher
from countdown_credentials.registration import RendererRegistration

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "config_store.json").read_text())
DEVICE_ID = FIXTURE["device_id"]
BROKER_URL = "https://broker.example.com"
CONFIG_URL = f"{BROKER_URL}/api/config"
STORED = FIXTURE["value"]


@pytest.fixture
def server(monkeypatch):
    """Return the fake Redis server the renderer's store (reader.store) is on, so an async client can share it."""
    shared = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=shared, decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: client)
    return shared


@pytest.fixture
def store(server):
    return reader.store()


def _broker() -> BrokerClient:
    return BrokerClient(RendererRegistration(BROKER_URL, "shh", DEVICE_ID))


def _put(store, value: dict) -> None:
    store.set(keys.config(DEVICE_ID), json.dumps(value))


# --- the stored shape ---------------------------------------------------------------------------------------------


def test_the_broker_writes_where_the_renderer_reads():
    assert keys.config(DEVICE_ID) == FIXTURE["key"]
    assert FIXTURE["channel"] == keys.CONFIG_CHANGED


def test_the_stored_config_is_a_config_the_contract_allows():
    config = ConfigResponse.model_validate(STORED)

    assert config.device_id == DEVICE_ID
    assert config.pairing_code is None
    # No key of any kind: the weather provider calls Open-Meteo, which needs none, and TfL's providers have their own.
    assert "api_key" not in config.weather.model_dump()
    assert config.weather.key == "london-gb-2643743"
    assert [stop.id for stop in config.departures.stops] == ["490000077E", "940GZZLUOVL", "NSR:StopPlace:58381"]


# --- reading it ---------------------------------------------------------------------------------------------------


@responses.activate
def test_the_config_comes_from_the_store_without_asking_the_broker(store):
    _put(store, STORED)

    config = _broker().get_config()

    assert config == ConfigResponse.model_validate(STORED)
    assert len(responses.calls) == 0  # responses would refuse any request anyway


@responses.activate
def test_boot_reads_the_store_too(store):
    _put(store, STORED)

    config, pairing_code_panel = _broker().fetch_app_config()

    assert config.device_id == DEVICE_ID
    assert pairing_code_panel.pairing_code is None
    assert len(responses.calls) == 0


@responses.activate
def test_without_a_config_in_the_store_the_broker_is_asked(store):
    """An older broker that writes none, or a pairing code to show, which only GET /api/config has."""
    responses.get(CONFIG_URL, json=broker_payload(device_id=DEVICE_ID, pairing_code="ABC234"))

    config = _broker().get_config()

    assert config.pairing_code == "ABC234"
    assert len(responses.calls) == 1


@responses.activate
def test_without_a_store_the_broker_is_asked(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    responses.get(CONFIG_URL, json=broker_payload(device_id=DEVICE_ID))

    assert _broker().get_config().device_id == DEVICE_ID
    assert len(responses.calls) == 1


@responses.activate
def test_a_store_that_cant_be_read_means_asking_the_broker(store, monkeypatch):
    def refuse(key):
        raise redis.ConnectionError

    monkeypatch.setattr(store, "get", refuse)
    responses.get(CONFIG_URL, json=broker_payload(device_id=DEVICE_ID, interval=42))

    with patch.object(broker_client, "logger") as logger:
        config = _broker().get_config()

    assert config.interval == 42
    logger.warning.assert_called_once_with(
        "Can't read the config from the store (%s); asking the broker", "ConnectionError"
    )


@responses.activate
def test_a_stored_config_that_isnt_valid_means_asking_the_broker(store):
    _put(store, STORED | {"interval": 0})  # not one the contract allows
    responses.get(CONFIG_URL, json=broker_payload(device_id=DEVICE_ID, interval=42))

    with patch.object(broker_client, "logger") as logger:
        config = _broker().get_config()

    assert config.interval == 42
    logger.warning.assert_called_once_with("The config in the store isn't valid for this renderer; asking the broker")


def test_a_field_a_newer_broker_adds_is_ignored(store):
    _put(store, STORED | {"brightness": 7})

    assert _broker().get_config() == ConfigResponse.model_validate(STORED)


@responses.activate
def test_once_paired_the_renderer_moves_from_the_broker_to_the_store(store):
    responses.get(CONFIG_URL, json=broker_payload(device_id=DEVICE_ID, pairing_code="ABC234"))
    broker = _broker()

    with patch.object(broker_client, "logger") as logger:
        assert broker.get_config().pairing_code == "ABC234"
        assert broker.get_config().pairing_code == "ABC234"
        _put(store, STORED)  # the code was typed in: the broker stores the config, now there's no code
        assert broker.get_config().pairing_code is None
        assert broker.get_config().pairing_code is None

    # Logged only when the source changes.
    assert [c.args for c in logger.info.call_args_list] == [
        ("Reading the config from %s", "the broker (there's none for it in the store)"),
        ("Reading the config from %s", "the store"),
    ]
    assert broker.get_pairing_code_panel(broker.get_config()).pairing_code is None


def test_read_gives_none_for_a_device_with_nothing_stored(store):
    assert stored_config.read(store, "someone-else") is None


# --- the nudge ----------------------------------------------------------------------------------------------------


def _watcher(server, device_id=DEVICE_ID) -> ConfigWatcher:
    return ConfigWatcher(fakeredis.aioredis.FakeRedis(server=server, decode_responses=True), device_id)


async def _until(condition):
    """Wait until `condition()` holds, failing after 5 s."""
    async with asyncio.timeout(5):
        while not condition():  # noqa: ASYNC110 -- what's awaited (a subscription, a draw) sets no event of ours
            await asyncio.sleep(0.01)


def test_the_watcher_wakes_for_its_own_device_only(server, store):
    async def scenario():
        watcher = _watcher(server)
        watcher.start()
        await _until(lambda: store.pubsub_numsub(keys.CONFIG_CHANGED)[0][1] == 1)
        try:
            store.publish(keys.CONFIG_CHANGED, "someone-else")
            woken_by_another = await watcher.wait(0.2)
            store.publish(keys.CONFIG_CHANGED, DEVICE_ID)
            woken_by_its_own = await watcher.wait(5)
            nothing_since = await watcher.wait(0.05)
        finally:
            await watcher.close()
        return woken_by_another, woken_by_its_own, nothing_since

    assert asyncio.run(scenario()) == (False, True, False)


def test_a_nudge_while_the_loop_is_busy_ends_its_next_wait_at_once(server, store):
    async def scenario():
        watcher = _watcher(server)
        watcher.start()
        await _until(lambda: store.pubsub_numsub(keys.CONFIG_CHANGED)[0][1] == 1)
        try:
            store.publish(keys.CONFIG_CHANGED, DEVICE_ID)
            await _until(watcher._changed.is_set)
            return await watcher.wait(3600)
        finally:
            await watcher.close()

    assert asyncio.run(scenario()) is True


def test_there_is_no_watcher_without_a_store(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    assert ConfigWatcher.from_env(DEVICE_ID) is None


def test_the_watcher_listens_on_the_renderers_store(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")

    watcher = ConfigWatcher.from_env(DEVICE_ID)

    kwargs = watcher.client.connection_pool.connection_kwargs
    assert (kwargs["host"], kwargs["password"], kwargs.get("socket_timeout")) == ("127.0.0.1", "secret", None)
    asyncio.run(watcher.client.aclose())


def test_the_watcher_resubscribes_after_losing_redis(server, store, monkeypatch):
    monkeypatch.setattr(stored_config, "RESUBSCRIBE_S", 0.01)
    watcher = _watcher(server)
    real_pubsub = watcher.client.pubsub
    failures = [redis.ConnectionError("gone")]

    def pubsub():
        if failures:
            raise failures.pop()
        return real_pubsub()

    monkeypatch.setattr(watcher.client, "pubsub", pubsub)

    async def scenario():
        with patch.object(stored_config, "logger") as logger:
            watcher.start()
            await _until(lambda: store.pubsub_numsub(keys.CONFIG_CHANGED)[0][1] == 1)
            store.publish(keys.CONFIG_CHANGED, DEVICE_ID)
            try:
                return await watcher.wait(5), logger.warning.call_args.args
            finally:
                await watcher.close()

    woken, warning = asyncio.run(scenario())

    assert woken is True
    assert warning == ("Lost %s (%s); resubscribing", keys.CONFIG_CHANGED, "ConnectionError")


def test_a_nudge_redraws_with_the_new_config_without_waiting_for_the_minute(server, store, monkeypatch):
    """The owner saves on the settings page: the broker rewrites the key and nudges, and the screen is redrawn."""
    # Nothing on the screen, so no client has anything to read. The next minute is up to 60 s off; the test gives 5.
    quiet = broker_payload(device_id=DEVICE_ID, interval=60, style="hero")
    _put(store, quiet)
    broker = _broker()
    config = broker.get_config()
    loop = DisplayLoop(broker, config, DisplayController(FakeTarget()), watcher=_watcher(server))
    drawn: list[str] = []
    render = display_loop.render

    def recording(device_id, now, **kwargs):
        drawn.append(kwargs["config"].style)
        return render(device_id, now, **kwargs)

    monkeypatch.setattr(display_loop, "render", recording)

    async def scenario():
        running = asyncio.create_task(loop.run())
        try:
            await _until(lambda: drawn and store.pubsub_numsub(keys.CONFIG_CHANGED)[0][1] == 1)
            _put(store, quiet | {"style": "card"})
            store.publish(keys.CONFIG_CHANGED, DEVICE_ID)
            await _until(lambda: len(drawn) == 2)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())

    assert drawn == ["hero", "card"]
    assert loop.watcher._task is None  # closed with the loop
