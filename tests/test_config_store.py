"""The screen's config from the store: countdown-server's copy of the device's settings, which the render reads.

The broker writes `v1:config:<device id>` from its database on every save and as it starts, and the render reads it
(store.config.read) with the contract's own ConfigResponse: it's the only way a config reaches the render.
The store contract's config_store.json (countdown_api.fixtures) is the stored shape, which the broker's tests read too.

What the render accepts is the compatibility policy: the broker may add fields (ignored), but a value outside what the
contract lists is invalid, and so is a config missing a section.
"""

import json

import fakeredis
import pytest
from config_factory import broker_payload
from pydantic import ValidationError

from countdown_api import fixtures
from countdown_api.models import ConfigResponse
from countdown_core.store import config as stored_config
from countdown_core.store import keys
from countdown_renderer.weather.weather_client import location_key

FIXTURE = fixtures.load("config_store.json")
DEVICE_ID = FIXTURE["device_id"]
STORED = FIXTURE["value"]


@pytest.fixture
def store():
    return fakeredis.FakeRedis(decode_responses=True)


def _put(store, value: dict) -> None:
    store.set(keys.config(DEVICE_ID), json.dumps(value))


def _read(store, value: dict) -> ConfigResponse | None:
    _put(store, value)
    return stored_config.read(store, DEVICE_ID)


# --- the stored shape ---------------------------------------------------------------------------------------------


def test_the_broker_writes_where_the_render_reads():
    assert keys.config(DEVICE_ID) == FIXTURE["key"]


def test_the_stored_config_is_a_config_the_contract_allows():
    config = ConfigResponse.model_validate(STORED)

    assert config.device_id == DEVICE_ID
    assert config.pairing_code is None
    # No key of any kind: the weather provider calls Open-Meteo, which needs none, and TfL's providers have their own.
    assert "api_key" not in config.weather.model_dump()
    assert config.weather.key == "london-gb-2643743"
    assert [stop.id for stop in config.departures.stops] == ["490000077E", "940GZZLUOVL", "NSR:StopPlace:58381"]


# --- reading it ---------------------------------------------------------------------------------------------------


def test_the_config_is_read_from_the_store(store):
    assert _read(store, STORED) == ConfigResponse.model_validate(STORED)


def test_read_gives_none_for_a_device_with_nothing_stored(store):
    _put(store, STORED)

    assert stored_config.read(store, "someone-else") is None


def test_a_full_config_reads_back_as_written(store):
    sent = ConfigResponse.model_validate(
        broker_payload(
            interval=20,
            screen={"orientation": "horizontal", "width": 800, "height": 480, "format": "mono-1bpp-packed"},
            weather={"location": "London", "key": "london-gb-2643743"},
            notice_board={"postcode": "SW1A 1AA"},
            tfl={"stop_ids": ["940GZZLUKNG", "490000077E"]},
            spotify={"enabled": True},
            glowmarkt={"enabled": True},
        )
    )

    assert _read(store, sent.model_dump(mode="json")) == sent


def test_a_field_a_newer_broker_adds_is_ignored(store):
    """The broker may add fields, to the config or to a section: a render that predates them must not break."""
    weather = STORED["weather"] | {"units": "metric"}

    assert _read(store, STORED | {"brightness": 7, "weather": weather}) == ConfigResponse.model_validate(STORED)


def test_a_broker_that_still_sends_a_weather_api_key_has_it_dropped(store):
    weather = STORED["weather"] | {"api_key": "weather-key"}

    config = _read(store, STORED | {"weather": weather})

    assert location_key(config.weather) == "london-gb-2643743"
    assert "weather-key" not in config.model_dump_json()


def test_without_the_weathers_key_it_reads_under_the_location(store):
    """An older broker subscribes the location's text, and sends no key, which parses as null."""
    weather = {name: value for name, value in STORED["weather"].items() if name != "key"}

    config = _read(store, STORED | {"weather": weather})

    assert config.weather.key is None
    assert location_key(config.weather) == config.weather.location


def test_a_value_the_contract_does_not_list_is_invalid(store):
    """A third screen orientation: widening a list of values is a breaking change the broker makes deliberately."""
    with pytest.raises(ValidationError):
        _read(store, STORED | {"screen": STORED["screen"] | {"orientation": "diagonal"}})


@pytest.mark.parametrize("interval", [0, -1])
def test_an_interval_that_is_not_positive_is_invalid(store, interval):
    with pytest.raises(ValidationError, match="interval"):
        _read(store, STORED | {"interval": interval})


def test_a_config_missing_a_section_is_invalid(store):
    """Rather than read as an empty config, which would blank the screen: the render fails, and it keeps its frame."""
    with pytest.raises(ValidationError, match="tfl"):
        _read(store, {name: value for name, value in STORED.items() if name != "tfl"})
