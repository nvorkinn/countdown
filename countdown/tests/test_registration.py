import asyncio
import dataclasses
import json
import logging
import stat

import pytest
import requests
import responses
from cryptography.fernet import Fernet
from test_utils import Sleeps, StopWaitingError

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
CLAIM_URL = f"{BROKER_URL}/api/renderers/claim"
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
    return RendererRegistrar(BROKER_URL, claim_token="claim-me", sleep=sleeps)


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
    broker.post(CLAIM_URL, status=201, json={"device_id": "device-123"})
    registrar = _renderer(sleeps)
    saved = _stored()["device_secret"]

    registration = _register(registrar)

    assert _bodies(broker, CLAIM_URL)[0]["secret"] == registration.device_secret == saved
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

    registrar = RendererRegistrar(BROKER_URL, claim_token="claim-me", sleep=sleeps, cipher=cipher)

    assert registrar._device_secret.encode() not in CREDENTIALS_FILE.read_bytes()
    assert _decrypted(cipher) == {"device_secret": registrar._device_secret}
    assert stat.S_IMODE(CREDENTIALS_FILE.stat().st_mode) == 0o600


def test_an_encrypted_file_is_read_back_with_the_same_key(sleeps):
    cipher = Fernet(Fernet.generate_key())
    first = RendererRegistrar(BROKER_URL, claim_token="claim-me", sleep=sleeps, cipher=cipher)
    first._claimed("device-123")

    registration = _register(RendererRegistrar(BROKER_URL, claim_token="claim-me", sleep=sleeps, cipher=cipher))

    assert registration == RendererRegistration(BROKER_URL, first._device_secret, "device-123")


def test_an_encrypted_file_with_the_wrong_key_is_an_error_not_a_new_identity(sleeps):
    RendererRegistrar(BROKER_URL, claim_token="claim-me", sleep=sleeps, cipher=Fernet(Fernet.generate_key()))
    before = CREDENTIALS_FILE.read_bytes()

    with pytest.raises(RuntimeError, match="wasn't encrypted with this device's credentials key"):
        RendererRegistrar(BROKER_URL, claim_token="claim-me", sleep=sleeps, cipher=Fernet(Fernet.generate_key()))

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

    assert _register(RendererRegistrar(f"{BROKER_URL}/", claim_token="claim-me", sleep=sleeps)).broker_url == BROKER_URL


# --- Renderer -----------------------------------------------------------------------


def test_a_registered_renderer_doesnt_call_the_broker(broker, sleeps):
    _seed(device_id="device-123", device_secret="shh")

    registration = _register(_renderer(sleeps))

    assert registration == RendererRegistration(BROKER_URL, "shh", "device-123")
    assert len(broker.calls) == 0
    assert sleeps == []


@pytest.mark.parametrize("status", [200, 201])
def test_a_renderer_claims_with_its_token_and_keeps_its_device_id(broker, sleeps, status):
    broker.post(CLAIM_URL, status=status, json={"device_id": "device-123"})

    registration = _register(_renderer(sleeps))

    assert registration.device_id == "device-123"
    assert _bodies(broker, CLAIM_URL) == [{"secret": registration.device_secret, "claim_token": "claim-me"}]
    assert broker.calls[0].request.req_kwargs["timeout"] == REQUEST_TIMEOUT_S
    assert _stored() == {"device_secret": registration.device_secret, "device_id": "device-123"}
    assert sleeps == []


@pytest.mark.parametrize("token", ["", None])
def test_a_renderer_cant_register_without_a_claim_token(token):
    with pytest.raises(ValueError, match="only register with a claim token"):
        RendererRegistrar(BROKER_URL, claim_token=token)


@pytest.mark.parametrize("status", [400, 403])
def test_a_rejected_claim_is_logged_and_retried_slowly(broker, sleeps, caplog, status):
    broker.post(CLAIM_URL, status=status, body="nope")
    broker.post(CLAIM_URL, status=201, json={"device_id": "device-123"})

    with caplog.at_level(logging.ERROR):
        registration = _register(_renderer(sleeps))

    assert "The broker rejected the registration: nope" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]
    assert registration.device_id == "device-123"


def test_a_renderer_whose_secret_is_a_screen_claims_again_with_a_new_secret(broker, sleeps):
    broker.post(CLAIM_URL, status=409)
    broker.post(CLAIM_URL, status=201, json={"device_id": "device-123"})
    renderer = _renderer(sleeps)
    first = renderer._device_secret

    registration = _register(renderer)

    assert registration.device_secret != first
    assert [body["secret"] for body in _bodies(broker, CLAIM_URL)] == [first, registration.device_secret]


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

ROLES = [pytest.param((_renderer, CLAIM_URL), id="renderer"), pytest.param((_display, REGISTER_URL), id="display")]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("status", [429, 503])
def test_waits_as_long_as_the_broker_says_then_retries(broker, sleeps, role, status):
    broker.post(role[1], status=status, headers={"Retry-After": "17"})
    broker.post(role[1], status=201, json={"device_id": "device-123"})

    _register(role[0](sleeps))

    assert sleeps == [17]
    assert len(broker.calls) == 2


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("status", [429, 503])
def test_waits_the_default_without_a_retry_after(broker, sleeps, role, status):
    broker.post(role[1], status=status)
    sleeps.stop_after = 1

    with pytest.raises(StopWaitingError):
        _register(role[0](sleeps))

    assert sleeps == [DEFAULT_RETRY_S]


@pytest.mark.parametrize("role", ROLES)
def test_retrying_reuses_the_saved_secret(broker, sleeps, role):
    broker.post(role[1], status=503)
    broker.post(role[1], status=201, json={"device_id": "device-123"})

    registration = _register(role[0](sleeps))

    first, second = (body["secret"] for body in _bodies(broker, role[1]))
    assert first == second == registration.device_secret == _stored()["device_secret"]


@pytest.mark.parametrize("role", ROLES)
def test_a_secret_taken_by_the_other_role_is_replaced_at_once(broker, sleeps, role):
    _seed(device_secret="shh")
    broker.post(role[1], status=409)
    broker.post(role[1], status=201, json={"device_id": "device-123"})

    registration = _register(role[0](sleeps))

    first, second = (body["secret"] for body in _bodies(broker, role[1]))
    assert first == "shh"
    assert second == registration.device_secret == _stored()["device_secret"] != "shh"
    assert broker.calls[1].request.headers["Authorization"] == f"Bearer {second}"
    assert sleeps == []


@pytest.mark.parametrize("role", ROLES)
def test_a_rejected_registration_is_logged_as_an_error(broker, sleeps, role, caplog):
    broker.post(role[1], status=400, json={"error": "role must be one of renderer, display"})
    sleeps.stop_after = 1

    with caplog.at_level(logging.ERROR), pytest.raises(StopWaitingError):
        _register(role[0](sleeps))

    assert "The broker rejected the registration" in caplog.text
    assert "role must be one of" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]


@pytest.mark.parametrize("role", ROLES)
def test_warns_about_an_unexpected_status(broker, sleeps, role, caplog):
    broker.post(role[1], status=418)
    sleeps.stop_after = 1

    with caplog.at_level(logging.WARNING), pytest.raises(StopWaitingError):
        _register(role[0](sleeps))

    assert f"Unexpected 418 from {role[1]}" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]


@pytest.mark.parametrize("role", ROLES)
def test_retries_when_the_broker_cant_be_reached(broker, sleeps, role, caplog):
    broker.post(role[1], body=requests.Timeout("read timed out"))
    broker.post(role[1], status=201, json={"device_id": "device-123"})

    with caplog.at_level(logging.WARNING):
        _register(role[0](sleeps))

    assert "Couldn't register with the broker: read timed out" in caplog.text
    assert sleeps == [DEFAULT_RETRY_S]
