import pytest
from config_factory import make_config
from pydantic import ValidationError

from countdown_core.config_server.models import AppConfig

# The `isolated_cwd` fixture used below is defined once, autouse, in tests/conftest.py.

SECTIONS = ["interval", "tfl", "spotify", "weather", "glowmarkt", "pairing_code"]


def test_an_empty_config_cannot_be_conjured():
    """An AppConfig only ever comes from a broker response (or, later, a cache of one): "no
    stops" must mean the broker said so, not that we never heard from it."""
    with pytest.raises(ValidationError) as error:
        AppConfig()

    missing = {e["loc"][0] for e in error.value.errors() if e["type"] == "missing"}
    assert missing == set(SECTIONS)


def test_an_empty_response_is_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({})


@pytest.mark.parametrize("section", SECTIONS)
def test_a_response_missing_any_section_is_rejected(section):
    """A broker deploy that renamed or dropped a section must not validate as "empty" and
    blank every device -- it fails, and the device keeps the config it had."""
    complete = make_config().model_dump()
    del complete[section]

    with pytest.raises(ValidationError, match=section):
        AppConfig.model_validate(complete)


def test_pairing_code_is_required_but_may_be_null():
    """Null is how the broker says "paired"; leaving the key out is a broken response."""
    assert make_config(pairing_code=None).pairing_code is None
    assert make_config(pairing_code="ABC123").pairing_code == "ABC123"


def test_settings_inside_a_section_can_still_default():
    config = make_config()
    assert config.tfl.app_key == ""
    assert config.tfl.stop_ids == []
    assert config.weather.location == ""
    assert config.spotify.enabled is False
    assert config.glowmarkt.username is None
    assert config.glowmarkt.password is None


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_cleared_glowmarkt_credential_is_not_set(blank):
    """The broker may send a cleared field as "" or whitespace rather than null."""
    config = make_config(glowmarkt={"username": blank, "password": blank})

    assert config.glowmarkt.username is None
    assert config.glowmarkt.password is None


def test_real_glowmarkt_credentials_are_kept_as_they_are():
    config = make_config(glowmarkt={"username": "me@example.com", "password": " pass word "})

    assert config.glowmarkt.username == "me@example.com"
    assert config.glowmarkt.password == " pass word "  # a password's spaces are part of it


@pytest.mark.parametrize(
    "credentials",
    [{}, {"username": "me@example.com"}, {"password": "hunter2"}, {"username": "  ", "password": "hunter2"}],
)
def test_glowmarkt_is_off_without_both_credentials(credentials):
    """The broker has no Glowmarkt switch -- it always sends enabled: true -- so the
    credentials decide."""
    assert make_config(glowmarkt={"enabled": True, **credentials}).glowmarkt.enabled is False


def test_glowmarkt_is_on_with_both_credentials():
    config = make_config(glowmarkt={"enabled": True, "username": "me@example.com", "password": "hunter2"})

    assert config.glowmarkt.enabled is True


def test_config_ignores_env_vars(isolated_cwd, monkeypatch):
    """AppConfig is deliberately not env/settings-sourced -- broker-owned fields should never
    pick up a local value, only ever what the broker sent."""
    monkeypatch.setenv("TFL__APP_KEY", "should-be-ignored")
    monkeypatch.setenv("GLOWMARKT__USERNAME", "should-be-ignored")
    monkeypatch.setenv("INTERVAL", "99")
    config = make_config()
    assert config.tfl.app_key == ""
    assert config.glowmarkt.username is None
    assert config.interval == 15
