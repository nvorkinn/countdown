import pytest
from config_factory import broker_payload, make_config
from pydantic import ValidationError

from countdown_api.models import ConfigResponse

# The `isolated_cwd` fixture used below is defined once, autouse, in tests/conftest.py.

SECTIONS = ["interval", "tfl", "spotify", "weather", "glowmarkt", "notice_board", "pairing_code"]


def test_an_empty_config_cannot_be_conjured():
    """A config only ever comes from a broker response (or, later, a cache of one): "no stops" must mean
    the broker said so, not that we never heard from it."""
    with pytest.raises(ValidationError) as error:
        ConfigResponse()

    missing = {e["loc"][0] for e in error.value.errors() if e["type"] == "missing"}
    assert set(SECTIONS) <= missing


@pytest.mark.parametrize("section", SECTIONS)
def test_a_response_missing_any_section_is_rejected(section):
    """A broker deploy that renamed or dropped a section must not validate as "empty" and
    blank every device -- it fails, and the device keeps the config it had."""
    payload = broker_payload()
    del payload[section]

    with pytest.raises(ValidationError, match=section):
        ConfigResponse.model_validate(payload)


@pytest.mark.parametrize("interval", [0, -1])
def test_an_interval_that_is_not_positive_is_rejected(interval):
    """The loop sleeps for it, so 0 would spin."""
    with pytest.raises(ValidationError, match="interval"):
        ConfigResponse.model_validate(broker_payload(interval=interval))


def test_pairing_code_is_required_but_may_be_null():
    """Null is how the broker says "paired"; leaving the key out is a broken response."""
    assert make_config(pairing_code=None).pairing_code is None
    assert make_config(pairing_code="ABC123").pairing_code == "ABC123"


def test_config_ignores_env_vars(isolated_cwd, monkeypatch):
    """The config is deliberately not env/settings-sourced -- broker-owned fields should never
    pick up a local value, only ever what the broker sent."""
    monkeypatch.setenv("TFL__APP_KEY", "should-be-ignored")
    monkeypatch.setenv("GLOWMARKT__USERNAME", "should-be-ignored")
    monkeypatch.setenv("INTERVAL", "99")
    config = make_config()
    assert config.tfl.app_key == ""
    assert config.glowmarkt.username is None
    assert config.interval == 15
