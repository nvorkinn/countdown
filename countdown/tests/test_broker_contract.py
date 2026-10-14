"""The broker's config API against what the app reads of it.

countdown_api (https://github.com/nvorkinn/countdown-api) provides the client and contract that auth-broker
implements. Nothing here talks to a broker: these fail when the
contract, the client built from it, or AppConfig stop agreeing, so a change on either side can't go unnoticed."""

from importlib.resources import files

import pytest
import responses
import yaml

from countdown_api import models as wire
from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.config_server.models import AppConfig
from countdown_credentials.registration import RendererRegistration

SPEC = files("countdown_api") / "openapi.yaml"

# Sent by the broker but not used by the renderer: its device_id comes from registration, and the screen's
# shape is the display's business.
UNUSED_BY_THE_APP = {"device_id", "screen"}
# In the app's config but not sent by the broker, by design. See models.py for each.
NOT_SENT_BY_THE_BROKER = {
    "tfl": {"enabled"},
    "spotify": set(),
    "weather": {"enabled"},
    "glowmarkt": {"enabled"},  # derived from the credentials
    "notice_board": {"enabled", "tfl"},  # tfl is a copy of AppConfig.tfl made on the device
}


def test_the_spec_describes_the_config_endpoint():
    spec = yaml.safe_load(SPEC.read_text())
    operation = spec["paths"]["/api/config"]["get"]
    assert operation["security"] == [{"deviceSecret": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ConfigResponse"
    }


@responses.activate
def test_the_headers_the_client_really_sends_are_the_ones_the_spec_declares(isolated_cwd, monkeypatch):
    """Authorization isn't a header parameter (the spec declares it as a security scheme); every other header the
    broker is sent on purpose must be declared, and a required one must be there."""
    from config_factory import broker_payload

    monkeypatch.setenv("DEVICE_NAME", "camilla")
    spec = yaml.safe_load(SPEC.read_text())
    declared = {
        p["name"]: p["required"] for p in spec["paths"]["/api/config"]["get"]["parameters"] if p["in"] == "header"
    }
    client = BrokerClient(RendererRegistration("https://broker.example.com", "shh", "device-123"))
    responses.add(responses.GET, "https://broker.example.com/api/config", json=broker_payload())

    client.get_config()

    sent = responses.calls[0].request.headers
    deliberate = {name for name in sent if name.lower().startswith("x-")}
    assert deliberate <= declared.keys(), "the client sends a header the spec doesn't declare"
    assert {name for name, required in declared.items() if required} <= deliberate, "a required header isn't sent"
    assert sent["Authorization"] == "Bearer shh"


def _fields(model) -> set[str]:
    return set(model.model_fields)


def test_every_top_level_field_the_broker_sends_is_either_read_or_known_to_be_unused():
    sent = _fields(wire.ConfigResponse)
    assert sent - _fields(AppConfig) == UNUSED_BY_THE_APP, (
        "the broker sends a field AppConfig doesn't read (or an unused one is now read): "
        "add it to AppConfig, or to UNUSED_BY_THE_APP"
    )


def test_every_field_the_app_reads_is_sent_by_the_broker():
    assert _fields(AppConfig) - _fields(wire.ConfigResponse) == set(), (
        "AppConfig expects a field the broker doesn't send"
    )


@pytest.mark.parametrize("section", ["tfl", "spotify", "weather", "glowmarkt", "notice_board"])
def test_a_section_matches_field_for_field(section):
    sent = _fields(wire.ConfigResponse.model_fields[section].annotation)
    read = _fields(AppConfig.model_fields[section].annotation)
    app_only = read - sent
    assert sent - read == set(), f"the broker sends {section} fields the app doesn't read"
    assert app_only <= NOT_SENT_BY_THE_BROKER[section], f"the app reads {section} fields the broker doesn't send"


def _full_response() -> wire.ConfigResponse:
    return wire.ConfigResponse(
        device_id="abc",
        interval=30,
        screen=wire.Screen(orientation="horizontal", width=800, height=480),
        weather=wire.Weather(api_key="weather-key", location="London"),
        notice_board=wire.NoticeBoard(postcode="SW1A 1AA"),
        tfl=wire.Tfl(app_key="tfl-key", stop_ids=["940GZZLUKNG", "490000077E"]),
        spotify=wire.Spotify(enabled=True),
        glowmarkt=wire.Glowmarkt(username="someone@example.com", password="hunter2"),
        pairing_code="ABC123",
        setup_missing=["a postcode"],
    )


def test_the_app_reads_a_full_response_as_sent():
    config = AppConfig.model_validate(_full_response().model_dump(mode="json"))

    assert config.interval == 30
    assert config.pairing_code == "ABC123"
    assert config.setup_missing == ["a postcode"]
    assert (config.weather.api_key, config.weather.location) == ("weather-key", "London")
    assert config.notice_board.postcode == "SW1A 1AA"
    assert (config.tfl.app_key, config.tfl.stop_ids) == ("tfl-key", ["940GZZLUKNG", "490000077E"])
    assert config.spotify.enabled is True
    assert (config.glowmarkt.username, config.glowmarkt.password) == ("someone@example.com", "hunter2")
    assert config.glowmarkt.enabled is True


def test_the_app_reads_a_response_for_a_device_with_nothing_set_up():
    response = _full_response().model_copy(
        update={
            "notice_board": wire.NoticeBoard(postcode=None),
            "tfl": wire.Tfl(app_key="", stop_ids=[]),
            "spotify": wire.Spotify(enabled=False),
            "glowmarkt": wire.Glowmarkt(username=None, password=None),
            "pairing_code": None,
        }
    )

    config = AppConfig.model_validate(response.model_dump(mode="json"))

    assert config.pairing_code is None
    assert config.glowmarkt.enabled is False
    assert config.notice_board.tfl.stop_ids == []
