import asyncio
from typing import TYPE_CHECKING

import pytest
import requests
import responses
from config_factory import broker_payload, make_config
from pydantic import ValidationError

from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.core import app
from countdown_credentials import device_name
from countdown_credentials.registration import RendererRegistration

if TYPE_CHECKING:
    from countdown_api.models import ConfigResponse

BROKER_URL = "https://broker.example.com"
CONFIG_URL = f"{BROKER_URL}/api/config"
DEFAULT_TIMEOUT = 10


def _app_config(pairing_code: str | None) -> ConfigResponse:
    return make_config(pairing_code=pairing_code)


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


@responses.activate
def test_get_config_uses_a_timeout(isolated_cwd):
    client = _initialised_client()
    responses.add(responses.GET, CONFIG_URL, json=broker_payload())

    client.get_config()

    assert responses.calls[0].request.req_kwargs["timeout"] == DEFAULT_TIMEOUT


@responses.activate
def test_get_config_sends_the_secret_as_a_bearer_token_and_no_body(isolated_cwd):
    client = _initialised_client("test-device", "test-secret")
    responses.add(responses.GET, CONFIG_URL, json=broker_payload())

    client.get_config()

    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer test-secret"
    assert request.body is None  # the contract declares none: the role is told by which secret it is


@responses.activate
def test_get_config_sends_device_name_header_from_device_name_env(isolated_cwd, monkeypatch):
    monkeypatch.setenv("DEVICE_NAME", "Sister HAT")
    client = _initialised_client()
    responses.add(responses.GET, CONFIG_URL, json=broker_payload())

    client.get_config()

    assert responses.calls[0].request.headers["X-Device-Name"] == "sister-hat"


@responses.activate
def test_get_config_sends_device_name_header_from_hostname_when_device_name_unset(isolated_cwd, monkeypatch):
    monkeypatch.setattr(device_name.socket, "gethostname", lambda: "vorkin-rbpi-z2w")
    client = _initialised_client()
    responses.add(responses.GET, CONFIG_URL, json=broker_payload())

    client.get_config()

    assert responses.calls[0].request.headers["X-Device-Name"] == "vorkin-rbpi-z2w"


@responses.activate
def test_get_config_omits_device_name_header_when_unresolvable(isolated_cwd, monkeypatch):
    """Never allowed to fail the request itself.

    A dev box with an unresolvable hostname and no DEVICE_NAME just doesn't send the (purely cosmetic) header.
    """
    monkeypatch.setattr(device_name.socket, "gethostname", lambda: "")
    client = _initialised_client()
    responses.add(responses.GET, CONFIG_URL, json=broker_payload())

    client.get_config()

    assert "X-Device-Name" not in responses.calls[0].request.headers


@responses.activate
def test_get_config_parses_response(isolated_cwd):
    client = _initialised_client()
    responses.add(
        responses.GET,
        CONFIG_URL,
        json=broker_payload(
            interval=20,
            tfl={"app_key": "tfl-key", "stop_ids": ["940GZZLUEUS"]},
            weather={"api_key": "weather-key", "location": "London"},
            spotify={"enabled": True},
            glowmarkt={"username": "me@example.com", "password": "hunter2"},
        ),
    )

    config = client.get_config()

    assert config.interval == 20
    assert config.tfl.stop_ids == ["940GZZLUEUS"]
    assert config.spotify.enabled is True
    assert config.glowmarkt.username == "me@example.com"
    assert config.pairing_code is None


@responses.activate
def test_get_config_accepts_null_glowmarkt_credentials(isolated_cwd):
    """The common case: a device whose owner hasn't set up Glowmarkt on the broker.

    Sent as null, not omitted -- must parse, not raise.
    """
    client = _initialised_client()
    responses.add(
        responses.GET,
        CONFIG_URL,
        json=broker_payload(glowmarkt={"username": None, "password": None}, pairing_code="ABC123"),
    )

    config = client.get_config()

    assert config.glowmarkt.username is None
    assert config.glowmarkt.password is None
    assert config.pairing_code == "ABC123"


@responses.activate
def test_get_config_ignores_a_field_a_newer_broker_adds(isolated_cwd):
    """The compatibility policy: the broker may add fields, to the response or to a section.

    A renderer that predates them must not break.
    """
    client = _initialised_client()
    payload = broker_payload()
    payload["a_field_from_the_future"] = {"anything": 1}
    payload["weather"] = {**payload["weather"], "units": "metric"}
    responses.add(responses.GET, CONFIG_URL, json=payload)

    assert client.get_config().interval == 15


@responses.activate
def test_get_config_rejects_a_value_the_contract_does_not_list(isolated_cwd):
    """The other half of the policy: a value outside the contract is invalid.

    Here it's a third screen orientation, so the config is rejected and the caller keeps the one it has. Widening a
    list of allowed values is a breaking change for the broker to make deliberately (see API_VERSION in auth-broker's
    contract.py).
    """
    client = _initialised_client()
    payload = broker_payload()
    payload["screen"] = {**payload["screen"], "orientation": "diagonal"}
    responses.add(responses.GET, CONFIG_URL, json=payload)

    with pytest.raises(ValidationError):
        client.get_config()


@responses.activate
@pytest.mark.parametrize("interval", [0, -1])
def test_get_config_rejects_an_interval_that_is_not_positive(isolated_cwd, interval):
    client = _initialised_client()
    responses.add(responses.GET, CONFIG_URL, json=broker_payload(interval=interval))

    with pytest.raises(ValidationError, match="interval"):
        client.get_config()


@responses.activate
def test_get_config_rejects_a_response_missing_a_section(isolated_cwd):
    """Rather than reading it as an empty config, which would blank the screen: the caller keeps the one it has."""
    client = _initialised_client()
    payload = broker_payload()
    del payload["tfl"]
    responses.add(responses.GET, CONFIG_URL, json=payload)

    with pytest.raises(ValidationError, match="tfl"):
        client.get_config()


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
        CONFIG_URL,
        json=broker_payload(
            interval=20,
            tfl={"app_key": "tfl-key", "stop_ids": ["940GZZLUEUS"]},
            weather={"api_key": "weather-key", "location": "London"},
            spotify={"enabled": True},
            pairing_code="XYZ789",
        ),
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
    """No empty fallback: with no config there's nothing sensible to run.

    The caller (app.wait_for_config) retries instead.
    """
    client = _initialised_client("test-device", "test-secret")
    # /config deliberately left unmocked -- responses raises ConnectionError for it.

    with pytest.raises(requests.exceptions.ConnectionError):
        client.fetch_app_config()


@responses.activate
def test_fetch_app_config_raises_when_response_is_invalid(isolated_cwd):
    client = _initialised_client("test-device", "test-secret")
    responses.add(responses.GET, CONFIG_URL, json={"interval": "soon"})

    with pytest.raises(ValidationError):
        client.fetch_app_config()


@responses.activate
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, {"error": "unauthorized"}),  # the contract's own error object
        (403, {"error": "forbidden"}),
        (404, {"error": "not found"}),
        (500, "internal server error"),  # not in the contract
    ],
)
def test_a_bad_status_from_the_broker_is_an_http_error_the_caller_retries_on(isolated_cwd, status, body):
    """app.wait_for_config retries on requests' RequestException.

    The generated client raises its own ApiError, so get_config turns it back into the exception callers already
    handle, keeping the response.
    """
    client = _initialised_client("test-device", "test-secret")
    responses.add(
        responses.GET, CONFIG_URL, **({"json": body} if isinstance(body, dict) else {"body": body}), status=status
    )

    with pytest.raises(requests.HTTPError) as error:
        client.fetch_app_config()

    assert isinstance(error.value, requests.exceptions.RequestException)
    assert error.value.response.status_code == status


@responses.activate
def test_a_gateway_error_that_does_not_recover_is_still_a_request_exception(isolated_cwd):
    """The session retries 502/503/504 itself (abstract_client) and gives up with RetryError.

    That's before the generated client sees a status. Callers catch RequestException, so a broker that is restarting
    is retried.
    """
    client = _initialised_client("test-device", "test-secret")
    responses.add(responses.GET, CONFIG_URL, body="<html><body>Bad Gateway</body></html>", status=502)

    with pytest.raises(requests.exceptions.RequestException):
        client.fetch_app_config()


@responses.activate
def test_a_failed_fetch_does_not_mark_the_pairing_code_as_seen(isolated_cwd):
    """The first *successful* fetch is still the one that reports has_changed."""
    client = _initialised_client("test-device", "test-secret")
    with pytest.raises(requests.exceptions.ConnectionError):
        client.fetch_app_config()
    responses.add(responses.GET, CONFIG_URL, json=broker_payload(pairing_code="ABC123"))

    _, pairing_code_panel = client.fetch_app_config()

    assert pairing_code_panel.pairing_code == "ABC123"
    assert pairing_code_panel.has_changed is True


@responses.activate
def test_the_boot_loop_retries_through_a_bad_status_instead_of_crashing(isolated_cwd):
    """The real BrokerClient through the real app.wait_for_config.

    The broker answers 404 once (say it is being redeployed), then the config. The renderer must wait and try again,
    not die at startup.
    """
    client = _initialised_client("test-device", "test-secret")
    responses.add(responses.GET, CONFIG_URL, json={"error": "not found"}, status=404)
    responses.add(responses.GET, CONFIG_URL, json=broker_payload(pairing_code="ABC123"))
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    config, pairing_code_panel = asyncio.run(app.wait_for_config(client, sleep=sleep))

    assert len(sleeps) == 1  # it waited once, then got the config
    assert config.pairing_code == "ABC123"
    assert pairing_code_panel.pairing_code == "ABC123"
