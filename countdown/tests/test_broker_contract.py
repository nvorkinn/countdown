"""The broker's config API against what the app does with it.

countdown_api (https://github.com/nvorkinn/countdown-api) provides the client and contract that auth-broker
implements, and the app reads the contract's own ConfigResponse. Nothing here talks to a broker: these fail when
the contract and the client built from it stop agreeing with what the app sends and relies on.
"""

from importlib.resources import files
from typing import TYPE_CHECKING

import responses
import yaml
from config_factory import broker_payload, make_config

from countdown_core.config_server.broker_client import BrokerClient
from countdown_credentials.registration import RendererRegistration

if TYPE_CHECKING:
    from countdown_api.models import ConfigResponse

SPEC = files("countdown_api") / "openapi.yaml"


def test_the_spec_describes_the_config_endpoint():
    spec = yaml.safe_load(SPEC.read_text())
    operation = spec["paths"]["/api/config"]["get"]
    assert operation["security"] == [{"deviceSecret": ["renderer"]}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ConfigResponse"
    }


@responses.activate
def test_the_headers_the_client_really_sends_are_the_ones_the_spec_declares(isolated_cwd, monkeypatch):
    """Every header the broker is sent on purpose must be declared, and a required one must be there.

    Authorization isn't a header parameter: the spec declares it as a security scheme.
    """
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


def _full_response() -> ConfigResponse:
    return make_config(
        device_id="abc",
        interval=30,
        screen={"orientation": "horizontal", "width": 800, "height": 480},
        weather={"api_key": "weather-key", "location": "London"},
        notice_board={"postcode": "SW1A 1AA"},
        tfl={"stop_ids": ["940GZZLUKNG", "490000077E"]},
        spotify={"enabled": True},
        glowmarkt={"enabled": True},
        pairing_code="ABC123",
    )


@responses.activate
def test_the_app_gets_a_full_response_as_sent(isolated_cwd):
    sent = _full_response()
    responses.add(responses.GET, "https://broker.example.com/api/config", json=sent.model_dump(mode="json"))
    client = BrokerClient(RendererRegistration("https://broker.example.com", "shh", "device-123"))

    assert client.get_config() == sent
