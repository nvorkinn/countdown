import asyncio
import dataclasses
import json
import logging
import stat

import pytest
import requests
import responses
from cryptography.fernet import Fernet
from test_utils import Sleeps, StopWaiting

from countdown_credentials.credentials_key import KEY_NAME, credentials_key
from countdown_credentials.registration import (
    CREDENTIALS_FILE,
    DEFAULT_RETRY_S,
    REQUEST_TIMEOUT_S,
    DisplayRegistrar,
    Registration,
    RendererRegistrar,
    RendererRegistration,
)

BROKER_URL = "https://broker.example.com"
REGISTER_URL = f"{BROKER_URL}/api/devices/register"
CONFIG_URL = f"{BROKER_URL}/api/config"


@pytest.fixture
def broker():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


def _seed(**data: str) -> None:
    CREDENTIALS_FILE.write_bytes(credentials_key().encrypt(json.dumps(data).encode()))


def _stored() -> dict:
    return json.loads(credentials_key().decrypt(CREDENTIALS_FILE.read_bytes()))


def _renderer(sleeps: Sleeps) -> RendererRegistrar:
    return RendererRegistrar(BROKER_URL, sleep=sleeps)


def _display(sleeps: Sleeps) -> DisplayRegistrar:
    return DisplayRegistrar(BROKER_URL, sleep=sleeps)


def _register(registrar):
    return asyncio.run(registrar.register())


def _bodies(broker, url: str = REGISTER_URL) -> list[dict]:
    return [json.loads(c.request.body) for c in broker.calls if c.request.url == url]


def _urls(broker) -> list[str]:
    return [c.request.url for c in broker.calls]


# --- Registration -------------------------------------------------------------------


def test_a_registration_cant_be_changed():
    registration = RendererRegistration(BROKER_URL, "shh", "device-123")

    with pytest.raises(dataclasses.FrozenInstanceError):
        registration.device_secret = "rotated"


def test_auth_sends_the_secret_on_any_session(broker):
    broker.get(CONFIG_URL)
    session = requests.Session()
    session.auth = Registration(BROKER_URL, "shh").auth

    session.get(CONFIG_URL)

    assert broker.calls[0].request.headers["Authorization"] == "Bearer shh"


# --- The credentials file -----------------------------------------------------------


def test_a_new_secret_is_saved_before_anything_is_sent(broker, sleeps):
    _renderer(sleeps)

    assert len(broker.calls) == 0
    assert set(_stored()) == {"device_secret"}
    assert _stored()["device_secret"]


def test_the_saved_secret_is_the_one_sent(broker, sleeps):
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})
    registrar = _renderer(sleeps)
    saved = _stored()["device_secret"]

    registration = _register(registrar)

    assert _bodies(broker)[0]["secret"] == registration.device_secret == saved
    assert broker.calls[0].request.headers["Authorization"] == f"Bearer {saved}"


def test_the_file_is_readable_by_its_owner_only(sleeps):
    _renderer(sleeps)

    assert stat.S_IMODE(CREDENTIALS_FILE.stat().st_mode) == 0o600


def test_a_file_with_only_a_secret_keeps_it(broker, sleeps):
    broker.post(REGISTER_URL, status=201)
    _seed(device_secret="shh")

    assert _register(_display(sleeps)).device_secret == "shh"


def test_extra_keys_in_the_file_are_ignored(sleeps):
    _seed(device_id="a", device_secret="b", registered_at="2026-10-04")

    assert _register(_renderer(sleeps)) == RendererRegistration(BROKER_URL, "b", "a")


def test_the_file_can_live_elsewhere(isolated_cwd, sleeps):
    path = isolated_cwd / "elsewhere.json"

    DisplayRegistrar(BROKER_URL, credentials_file=path, sleep=sleeps)

    assert set(json.loads(credentials_key().decrypt(path.read_bytes()))) == {"device_secret"}
    assert not CREDENTIALS_FILE.exists()


# --- Encrypting the credentials file ------------------------------------------------


def _decrypted(cipher: Fernet) -> dict:
    return json.loads(cipher.decrypt(CREDENTIALS_FILE.read_bytes()))


def test_with_a_key_the_file_is_encrypted_at_rest(sleeps):
    cipher = Fernet(Fernet.generate_key())

    registrar = RendererRegistrar(BROKER_URL, sleep=sleeps, cipher=cipher)

    assert registrar._device_secret.encode() not in CREDENTIALS_FILE.read_bytes()
    assert _decrypted(cipher) == {"device_secret": registrar._device_secret}
    assert stat.S_IMODE(CREDENTIALS_FILE.stat().st_mode) == 0o600


def test_an_encrypted_file_is_read_back_with_the_same_key(sleeps):
    cipher = Fernet(Fernet.generate_key())
    first = RendererRegistrar(BROKER_URL, sleep=sleeps, cipher=cipher)
    first._matched("device-123")

    registration = _register(RendererRegistrar(BROKER_URL, sleep=sleeps, cipher=cipher))

    assert registration == RendererRegistration(BROKER_URL, first._device_secret, "device-123")


def test_an_encrypted_file_with_the_wrong_key_is_an_error_not_a_new_identity(sleeps):
    RendererRegistrar(BROKER_URL, sleep=sleeps, cipher=Fernet(Fernet.generate_key()))
    before = CREDENTIALS_FILE.read_bytes()

    with pytest.raises(RuntimeError, match="wasn't encrypted with this device's credentials key"):
        RendererRegistrar(BROKER_URL, sleep=sleeps, cipher=Fernet(Fernet.generate_key()))

    assert CREDENTIALS_FILE.read_bytes() == before


def test_the_key_comes_from_systemds_credentials_directory(isolated_cwd, monkeypatch, sleeps):
    key = Fernet.generate_key()
    directory = isolated_cwd / "creds"
    directory.mkdir()
    (directory / KEY_NAME).write_bytes(key + b"\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(directory))

    registrar = _renderer(sleeps)

    assert _decrypted(Fernet(key)) == {"device_secret": registrar._device_secret}


def test_without_a_key_one_is_made_for_the_user_and_kept(isolated_cwd, sleeps):
    registrar = _renderer(sleeps)
    key_file = isolated_cwd / "config" / "countdown" / KEY_NAME

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert _decrypted(Fernet(key_file.read_bytes())) == {"device_secret": registrar._device_secret}
    assert _renderer(sleeps)._device_secret == registrar._device_secret  # read back with the same key


def test_a_trailing_slash_on_the_broker_url_is_dropped(sleeps):
    _seed(device_id="a", device_secret="b")

    assert _register(RendererRegistrar(f"{BROKER_URL}/", sleep=sleeps)).broker_url == BROKER_URL


# --- Renderer -----------------------------------------------------------------------


def test_a_registered_renderer_doesnt_call_the_broker(broker, sleeps):
    _seed(device_id="device-123", device_secret="shh")

    registration = _register(_renderer(sleeps))

    assert registration == RendererRegistration(BROKER_URL, "shh", "device-123")
    assert len(broker.calls) == 0
    assert sleeps == []


@pytest.mark.parametrize("status", [200, 201])
def test_a_renderer_registers_and_keeps_its_device_id(broker, sleeps, status):
    broker.post(REGISTER_URL, status=status, json={"device_id": "device-123"})

    registration = _register(_renderer(sleeps))

    assert registration.device_id == "device-123"
    assert _bodies(broker) == [{"role": "renderer", "secret": registration.device_secret}]
    assert broker.calls[0].request.req_kwargs["timeout"] == REQUEST_TIMEOUT_S
    assert _stored() == {"device_secret": registration.device_secret, "device_id": "device-123"}
    assert sleeps == []


def test_a_renderer_with_a_claim_token_sends_it_to_be_matched_at_once(broker, sleeps):
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})
    renderer = RendererRegistrar(BROKER_URL, claim_token="claim-me", sleep=sleeps)

    registration = _register(renderer)

    assert _bodies(broker) == [{"role": "renderer", "secret": registration.device_secret, "claim_token": "claim-me"}]
    assert registration.device_id == "device-123"
    assert sleeps == []


def test_an_empty_claim_token_is_not_sent(broker, sleeps):
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})

    _register(RendererRegistrar(BROKER_URL, claim_token="", sleep=sleeps))

    assert "claim_token" not in _bodies(broker)[0]


def test_a_renderer_waiting_to_be_matched_polls_the_config_until_it_has_a_device_id(broker, sleeps):
    broker.post(REGISTER_URL, status=202, headers={"Retry-After": "30"})
    broker.get(CONFIG_URL, status=202, headers={"Retry-After": "30"})
    broker.get(CONFIG_URL, status=202, headers={"Retry-After": "15"})
    broker.get(CONFIG_URL, status=200, json={"device_id": "device-123", "interval": 60})

    registration = _register(_renderer(sleeps))

    assert _urls(broker) == [REGISTER_URL, CONFIG_URL, CONFIG_URL, CONFIG_URL]  # /register only once
    assert _bodies(broker, CONFIG_URL) == [{"role": "renderer"}] * 3
    assert sleeps == [30, 15]
    assert registration.device_id == "device-123"
    assert _stored()["device_id"] == "device-123"


def test_a_renderer_dropped_from_the_pool_registers_again_with_the_same_secret(broker, sleeps):
    broker.post(REGISTER_URL, status=202)
    broker.get(CONFIG_URL, status=401)
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})

    registration = _register(_renderer(sleeps))

    assert _urls(broker) == [REGISTER_URL, CONFIG_URL, REGISTER_URL]
    assert [body["secret"] for body in _bodies(broker)] == [registration.device_secret] * 2
    assert registration.device_id == "device-123"


def test_a_renderer_keeps_waiting_through_network_errors(broker, sleeps, caplog):
    broker.post(REGISTER_URL, status=202)
    broker.get(CONFIG_URL, body=requests.ConnectionError("no route to host"))
    broker.get(CONFIG_URL, status=200, json={"device_id": "device-123"})

    with caplog.at_level(logging.WARNING):
        registration = _register(_renderer(sleeps))

    assert "Couldn't reach the broker: no route to host" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]
    assert registration.device_id == "device-123"


def test_a_renderer_warns_about_an_unexpected_status_while_waiting(broker, sleeps, caplog):
    broker.post(REGISTER_URL, status=202)
    broker.get(CONFIG_URL, status=500)
    broker.get(CONFIG_URL, status=200, json={"device_id": "device-123"})

    with caplog.at_level(logging.WARNING):
        _register(_renderer(sleeps))

    assert f"Unexpected 500 from {CONFIG_URL}" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]


# --- Display ------------------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201, 202])
def test_a_display_registers_and_goes_straight_on(broker, sleeps, status):
    broker.post(REGISTER_URL, status=status, json={"device_id": "device-123"})

    registration = _register(_display(sleeps))

    assert type(registration) is Registration  # no device_id: a display never needs one
    assert _urls(broker) == [REGISTER_URL]  # no waiting to be matched
    assert _bodies(broker) == [{"role": "display", "secret": registration.device_secret}]
    assert _stored() == {"device_secret": registration.device_secret}
    assert sleeps == []


def test_a_display_registers_on_every_boot(broker, sleeps):
    broker.post(REGISTER_URL, status=200)
    _seed(device_id="device-123", device_secret="shh")

    _register(_display(sleeps))

    assert _bodies(broker) == [{"role": "display", "secret": "shh"}]


# --- What both roles do with /register's answers ------------------------------------

ROLES = [pytest.param(_renderer, id="renderer"), pytest.param(_display, id="display")]


@pytest.mark.parametrize("make", ROLES)
@pytest.mark.parametrize("status", [429, 503])
def test_waits_as_long_as_the_broker_says_then_retries(broker, sleeps, make, status):
    broker.post(REGISTER_URL, status=status, headers={"Retry-After": "17"})
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})

    _register(make(sleeps))

    assert sleeps == [17]
    assert len(broker.calls) == 2


@pytest.mark.parametrize("make", ROLES)
@pytest.mark.parametrize("status", [429, 503])
def test_waits_the_default_without_a_retry_after(broker, sleeps, make, status):
    broker.post(REGISTER_URL, status=status)
    sleeps.stop_after = 1

    with pytest.raises(StopWaiting):
        _register(make(sleeps))

    assert sleeps == [DEFAULT_RETRY_S]


@pytest.mark.parametrize("make", ROLES)
def test_retrying_reuses_the_saved_secret(broker, sleeps, make):
    broker.post(REGISTER_URL, status=503)
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})

    registration = _register(make(sleeps))

    first, second = (body["secret"] for body in _bodies(broker))
    assert first == second == registration.device_secret == _stored()["device_secret"]


@pytest.mark.parametrize("make", ROLES)
def test_a_secret_taken_by_the_other_role_is_replaced_at_once(broker, sleeps, make):
    _seed(device_secret="shh")
    broker.post(REGISTER_URL, status=409)
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})

    registration = _register(make(sleeps))

    first, second = (body["secret"] for body in _bodies(broker))
    assert first == "shh"
    assert second == registration.device_secret == _stored()["device_secret"] != "shh"
    assert broker.calls[1].request.headers["Authorization"] == f"Bearer {second}"
    assert sleeps == []


@pytest.mark.parametrize("make", ROLES)
def test_a_rejected_registration_is_logged_as_an_error(broker, sleeps, make, caplog):
    broker.post(REGISTER_URL, status=400, json={"error": "role must be one of renderer, display"})
    sleeps.stop_after = 1

    with caplog.at_level(logging.ERROR), pytest.raises(StopWaiting):
        _register(make(sleeps))

    assert "The broker rejected the registration" in caplog.text
    assert "role must be one of" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]


@pytest.mark.parametrize("make", ROLES)
def test_warns_about_an_unexpected_status(broker, sleeps, make, caplog):
    broker.post(REGISTER_URL, status=418)
    sleeps.stop_after = 1

    with caplog.at_level(logging.WARNING), pytest.raises(StopWaiting):
        _register(make(sleeps))

    assert f"Unexpected 418 from {REGISTER_URL}" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]


@pytest.mark.parametrize("make", ROLES)
def test_retries_when_the_broker_cant_be_reached(broker, sleeps, make, caplog):
    broker.post(REGISTER_URL, body=requests.Timeout("read timed out"))
    broker.post(REGISTER_URL, status=201, json={"device_id": "device-123"})

    with caplog.at_level(logging.WARNING):
        _register(make(sleeps))

    assert "Couldn't register with the broker: read timed out" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]
