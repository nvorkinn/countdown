import pytest

from countdown_core.core import device_name
from countdown_core.core.device_name import device_name_from_env, resolve_device_name


def test_explicit_device_name_wins_over_hostname():
    assert resolve_device_name("sister-hat", "raspberrypi") == "sister-hat"


def test_device_name_falls_back_to_hostname_when_unset_or_blank():
    assert resolve_device_name(None, "vorkin-rbpi-z2w") == "vorkin-rbpi-z2w"
    assert resolve_device_name("  ", "pi") == "pi"


def test_device_name_is_sanitised_for_use_as_a_topic_level():
    assert resolve_device_name("Living Room/HAT#1+", None) == "living-room-hat-1-"


def test_device_name_errors_when_nothing_usable():
    with pytest.raises(ValueError, match="DEVICE_NAME is not set"):
        resolve_device_name(None, None)
    with pytest.raises(ValueError, match="has no usable characters"):
        resolve_device_name("///", None)


# --- From the environment ------------------------------------------------------------


@pytest.fixture
def hostname(monkeypatch):
    monkeypatch.setattr(device_name.socket, "gethostname", lambda: "raspberrypi")


def test_from_env_reads_device_name(monkeypatch, hostname):
    monkeypatch.setenv("DEVICE_NAME", "Sister HAT")

    assert device_name_from_env() == "sister-hat"


def test_from_env_skips_a_blank_device_name(monkeypatch, hostname):
    monkeypatch.setenv("DEVICE_NAME", "  ")

    assert device_name_from_env() == "raspberrypi"


def test_from_env_falls_back_to_the_hostname(hostname):
    assert device_name_from_env() == "raspberrypi"
