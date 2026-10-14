import asyncio
from unittest.mock import MagicMock

import pytest
import requests
import responses
from pydantic import ValidationError

from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.config_server.models import AppConfig
from countdown_credentials import device_name
from countdown_credentials.registration import RendererRegistration

BROKER_URL = "https://broker.example.com"
DEFAULT_TIMEOUT = 10


def _app_config(pairing_code: str | None) -> AppConfig:
    return AppConfig(
        interval=15,
        tfl={"app_key": "", "stop_ids": []},
        weather={"api_key": "", "location": ""},
        spotify={"enabled": False},
        glowmarkt={"username": None, "password": None},
        pairing_code=pairing_code,
        setup_missing=[],
    )


def _response(json_data: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = json_data
    return response


def _initialised_client(device_id: str = "device-123", device_secret: str = "shh") -> BrokerClient:
    client = BrokerClient(RendererRegistration(BROKER_URL, device_secret, device_id))
    asyncio.run(client.initialise())
    return client


@responses.activate
def test_initialising_makes_no_requests(isolated_cwd):

    _initialised_client()  # responses would raise ConnectionError for any request

    assert len(responses.calls) == 0


def test_talks_to_the_registrations_broker_as_its_device(isolated_cwd):
    client = _initialised_client("existing-device", "existing-secret")

    assert client.base_url == BROKER_URL
    assert client.device_id == "existing-device"


def test_request_returns_the_json_with_a_timeout(isolated_cwd):
    client = _initialised_client()

    response = _response({"ok": True})
    request_mock = MagicMock(return_value=response)
    client.session.request = request_mock

    result = client._request("GET", "/some/path")

    assert result == {"ok": True}
    assert request_mock.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


@responses.activate
def test_get_config_sends_the_secret_as_a_bearer_token_and_the_role_in_the_body(isolated_cwd):
    client = _initialised_client("test-device", "test-secret")
    responses.add(responses.GET, f"{BROKER_URL}/api/config", json=_app_config(None).model_dump())

    client.get_config()

    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer test-secret"
    assert request.body is None  # the secret goes in the body only when registering


def test_request_sends_device_name_header_from_device_name_env(isolated_cwd, monkeypatch):
    monkeypatch.setenv("DEVICE_NAME", "Sister HAT")
    client = _initialised_client()
    request_mock = MagicMock(return_value=_response({}))
    client.session.request = request_mock

    client._request("GET", "/some/path")

    assert request_mock.call_args.kwargs["headers"]["X-Device-Name"] == "sister-hat"


def test_request_sends_device_name_header_from_hostname_when_device_name_unset(isolated_cwd, monkeypatch):
    monkeypatch.setattr(device_name.socket, "gethostname", lambda: "vorkin-rbpi-z2w")
    client = _initialised_client()
    request_mock = MagicMock(return_value=_response({}))
    client.session.request = request_mock

    client._request("GET", "/some/path")

    assert request_mock.call_args.kwargs["headers"]["X-Device-Name"] == "vorkin-rbpi-z2w"


def test_request_omits_device_name_header_when_unresolvable(isolated_cwd, monkeypatch):
    """Never allowed to fail the request itself -- a dev box with an unresolvable
    hostname and no DEVICE_NAME just doesn't send the (purely cosmetic) header."""
    monkeypatch.setattr(device_name.socket, "gethostname", lambda: "")
    client = _initialised_client()
    request_mock = MagicMock(return_value=_response({}))
    client.session.request = request_mock

    client._request("GET", "/some/path")

    assert "X-Device-Name" not in request_mock.call_args.kwargs["headers"]


def test_get_config_parses_response(isolated_cwd):
    client = _initialised_client()
    client.session.request = MagicMock(
        return_value=_response(
            {
                "interval": 20,
                "tfl": {"app_key": "tfl-key", "stop_ids": ["940GZZLUEUS"]},
                "weather": {"api_key": "weather-key", "location": "London"},
                "spotify": {"enabled": True},
                "glowmarkt": {"username": "me@example.com", "password": "hunter2"},
                "pairing_code": None,
                "setup_missing": [],
            }
        )
    )

    config = client.get_config()

    assert config.interval == 20
    assert config.tfl.stop_ids == ["940GZZLUEUS"]
    assert config.spotify.enabled is True
    assert config.glowmarkt.username == "me@example.com"
    assert config.pairing_code is None


def test_get_config_accepts_null_glowmarkt_credentials(isolated_cwd):
    """The common case: a device whose owner hasn't set up Glowmarkt on the
    broker. Sent as null, not omitted -- must parse, not raise."""
    client = _initialised_client()
    client.session.request = MagicMock(
        return_value=_response(
            {
                "interval": 15,
                "tfl": {"app_key": "", "stop_ids": []},
                "weather": {"api_key": "", "location": ""},
                "spotify": {"enabled": False},
                "glowmarkt": {"username": None, "password": None},
                "pairing_code": "ABC123",
                "setup_missing": [],
            }
        )
    )

    config = client.get_config()

    assert config.glowmarkt.username is None
    assert config.glowmarkt.password is None
    assert config.pairing_code == "ABC123"


def test_get_pairing_code_panel_wraps_code_and_device_id(isolated_cwd):
    client = _initialised_client()

    panel = client.get_pairing_code_panel(_app_config("ABC123"))

    assert panel.pairing_code == "ABC123"
    assert panel.device_id == "device-123"


def test_get_pairing_code_panel_has_changed_false_on_repeat(isolated_cwd):
    """A paired device gets `pairing_code=None` on every poll; has_changed must stay False."""
    client = _initialised_client()

    first = client.get_pairing_code_panel(_app_config(None))
    second = client.get_pairing_code_panel(_app_config(None))

    assert first.has_changed is True  # first time this endpoint's been seen at all
    assert second.has_changed is False


def test_get_pairing_code_panel_has_changed_true_when_code_changes(isolated_cwd):
    client = _initialised_client()

    client.get_pairing_code_panel(_app_config("ABC123"))
    regenerated = client.get_pairing_code_panel(_app_config("XYZ789"))
    now_paired = client.get_pairing_code_panel(_app_config(None))

    assert regenerated.has_changed is True
    assert now_paired.has_changed is True


@responses.activate
def test_fetch_app_config_uses_real_broker_response(isolated_cwd):
    client = _initialised_client("test-device", "test-secret")
    responses.add(
        responses.GET,
        f"{BROKER_URL}/api/config",
        json={
            "interval": 20,
            "tfl": {"app_key": "tfl-key", "stop_ids": ["940GZZLUEUS"]},
            "weather": {"api_key": "weather-key", "location": "London"},
            "spotify": {"enabled": True},
            "glowmarkt": {"username": None, "password": None},
            "pairing_code": "XYZ789",
            "setup_missing": [],
        },
    )

    config, pairing_code_panel = client.fetch_app_config()

    assert config.interval == 20
    assert config.tfl.stop_ids == ["940GZZLUEUS"]
    assert config.spotify.enabled is True
    assert pairing_code_panel.pairing_code == "XYZ789"
    assert pairing_code_panel.device_id == "test-device"
    assert pairing_code_panel.has_changed is True  # first time this device's ever checked


@responses.activate
def test_fetch_app_config_raises_when_broker_unreachable(isolated_cwd):
    """No empty fallback: with no config there's nothing sensible to run, so the caller
    (app.wait_for_config) retries."""
    client = _initialised_client("test-device", "test-secret")
    # /config deliberately left unmocked -- responses raises ConnectionError for it.

    with pytest.raises(requests.exceptions.ConnectionError):
        client.fetch_app_config()


@responses.activate
def test_fetch_app_config_raises_when_response_is_invalid(isolated_cwd):
    client = _initialised_client("test-device", "test-secret")
    responses.add(responses.GET, f"{BROKER_URL}/api/config", json={"interval": "soon"})

    with pytest.raises(ValidationError):
        client.fetch_app_config()


@responses.activate
def test_a_failed_fetch_does_not_mark_the_pairing_code_as_seen(isolated_cwd):
    """The first *successful* fetch is still the one that reports has_changed."""
    client = _initialised_client("test-device", "test-secret")
    with pytest.raises(requests.exceptions.ConnectionError):
        client.fetch_app_config()
    responses.add(
        responses.GET,
        f"{BROKER_URL}/api/config",
        json=_app_config("ABC123").model_dump(),
    )

    _, pairing_code_panel = client.fetch_app_config()

    assert pairing_code_panel.pairing_code == "ABC123"
    assert pairing_code_panel.has_changed is True
