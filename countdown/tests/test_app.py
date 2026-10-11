import asyncio
import logging
import signal
from importlib.metadata import entry_points
from types import SimpleNamespace

import pytest
import requests
from config_factory import make_config
from pydantic import BaseModel, ValidationError

from countdown_core.config_server.models import AppConfig
from countdown_core.core import app
from countdown_core.core.display_loop import safe_fetch
from countdown_credentials.registration import RendererRegistrar, RendererRegistration
from countdown_server.server_target import ServerTarget


def test_safe_fetch_returns_func_result_on_success():
    assert safe_fetch(lambda: 42, fallback="fallback") == 42


def test_safe_fetch_returns_fallback_on_request_exception():
    def boom():
        raise requests.exceptions.ConnectionError("network down")

    assert safe_fetch(boom, fallback="fallback") == "fallback"


def test_safe_fetch_returns_fallback_on_validation_error():
    class Model(BaseModel):
        value: int

    def boom():
        Model.model_validate({"value": "not-an-int"})

    assert safe_fetch(boom, fallback="fallback") == "fallback"


def test_the_console_script_entry_point_actually_runs_the_app(monkeypatch):
    """The `countdown-server` script just calls main() without awaiting it, so main() must
    run the app itself. Every other test drives run() directly and wouldn't notice."""
    ran = []

    async def fake_run(make_target):
        ran.append(make_target)

    monkeypatch.setattr(app, "run", fake_run)
    # Keep the test run's own logging set-up as it is.
    monkeypatch.setattr(app, "configure_logging", lambda: None)
    (entry_point,) = entry_points(group="console_scripts", name="countdown-server")

    assert entry_point.load()() is None
    assert ran == [ServerTarget]


@pytest.mark.parametrize(
    ("env", "expected"), [(None, logging.INFO), ("debug", logging.DEBUG), ("nonsense", logging.INFO)]
)
def test_logging_defaults_to_info_and_honours_log_level(monkeypatch, env, expected):
    calls = []
    monkeypatch.setattr(app.logging, "basicConfig", lambda **kwargs: calls.append(kwargs))
    if env is None:
        monkeypatch.delenv("LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("LOG_LEVEL", env)

    app.configure_logging()

    (kwargs,) = calls
    assert kwargs["level"] == expected
    assert "%(filename)s:%(lineno)d" in kwargs["format"]
    assert "asctime" not in kwargs["format"]  # journald timestamps each line itself


# --- waiting for a config at boot ---------------------------------------------------


class FakeBroker:
    """Stands in for BrokerClient: `fetch_failures` are the exceptions to raise, in order,
    before it starts succeeding."""

    def __init__(self, fetch_failures=()):
        self.fetch_failures = list(fetch_failures)
        self.fetch_calls = 0

    def fetch_app_config(self):
        self.fetch_calls += 1
        if self.fetch_failures:
            raise self.fetch_failures.pop(0)
        return make_config(), "the pairing panel"


def wait(broker):
    delays: list[float] = []

    async def sleep(seconds):
        delays.append(seconds)
        assert len(delays) < 50, "wait_for_config is retrying forever"

    result = asyncio.run(app.wait_for_config(broker, sleep=sleep))
    return result, delays


def test_a_broker_that_answers_straight_away_means_no_waiting():
    (config, panel), delays = wait(FakeBroker())

    assert config == make_config()
    assert panel == "the pairing panel"
    assert delays == []


def test_an_unreachable_broker_is_retried_with_backoff():
    broker = FakeBroker(fetch_failures=[requests.exceptions.ConnectionError("down")] * 3)

    (config, _), delays = wait(broker)

    assert config == make_config()
    assert delays == [30, 60, 120]
    assert broker.fetch_calls == 4


def test_the_backoff_stops_growing_at_the_maximum():
    broker = FakeBroker(fetch_failures=[requests.exceptions.ConnectionError("down")] * 8)

    _, delays = wait(broker)

    assert delays == [30, 60, 120, 240, 300, 300, 300, 300]


def test_a_response_that_is_not_a_valid_config_is_treated_like_no_response():
    with pytest.raises(ValidationError) as error:
        AppConfig.model_validate({})
    broker = FakeBroker(fetch_failures=[error.value])

    (config, _), delays = wait(broker)

    assert config == make_config()
    assert delays == [30]


def test_a_real_bug_still_crashes_loudly_instead_of_being_retried_forever():
    broker = FakeBroker(fetch_failures=[TypeError("a bug")])

    with pytest.raises(TypeError):
        wait(broker)


# --- run() ----------------------------------------------------------------------------

REGISTRATION = RendererRegistration("https://broker.example.com", "shh", "device-123")


@pytest.fixture
def booted(monkeypatch):
    """Runs app.run() with the registration and everything after it faked, recording what it did."""
    monkeypatch.setenv("BROKER_URL", "https://broker.example.com")
    seen = {"order": []}

    class FakeLoop:
        def __init__(self, broker, config, pairing_code_panel, display, registry):
            seen.update(broker=broker, display=display, registry=registry)

        async def run(self):
            seen["order"].append("loop")

    async def fake_register(registrar):
        seen["order"].append("register")
        seen["registrar"] = registrar
        if seen.get("stop_while_registering"):
            seen["handlers"][signal.SIGTERM](signal.SIGTERM, None)
        return REGISTRATION

    def fake_signal(signum, handler):
        if not seen["handlers"]:
            seen["order"].append("signals")
        seen["handlers"][signum] = handler

    async def fake_wait(broker):
        seen["order"].append("config")
        return make_config(), "the pairing panel"

    def fake_start(publisher):
        seen["order"].append("publish")
        seen["publisher"] = publisher

    def make_target(registration):
        seen["order"].append("target")
        seen["target_registration"] = registration
        return "the target"

    fake_display = SimpleNamespace(panel_connected=True, shutdown=lambda: seen["order"].append("panel asleep"))
    monkeypatch.setattr(app.OtlpPublisher, "start", fake_start)
    monkeypatch.setattr(RendererRegistrar, "register", fake_register)
    monkeypatch.setattr(app, "DisplayController", lambda target: seen.update(target=target) or fake_display)
    monkeypatch.setattr(app, "DisplayLoop", FakeLoop)
    monkeypatch.setattr(app, "wait_for_config", fake_wait)
    monkeypatch.setattr(app.signal, "signal", fake_signal)
    seen["handlers"] = {}

    def boot(stop_while_registering=False):
        seen["stop_while_registering"] = stop_while_registering
        asyncio.run(app.run(make_target))
        return seen

    boot.seen = seen  # for a boot that's stopped part-way, which returns nothing

    return boot


def test_run_reports_to_home_assistant_from_the_start_and_does_nothing_else_until_registered(booted):
    seen = booted()

    assert seen["order"] == ["signals", "publish", "register", "target", "config", "loop"]


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_run_handles_stop_requests_before_anything_else(booted, signum):
    """In a container the app is PID 1, which ignores SIGTERM outright unless it has a handler."""
    seen = booted()

    assert seen["order"][0] == "signals"
    assert signum in seen["handlers"]


def test_stopping_while_registering_exits_with_no_display_to_sleep(booted):
    with pytest.raises(SystemExit) as stopped:
        booted(stop_while_registering=True)

    assert stopped.value.code == 0
    assert booted.seen["order"] == ["signals", "publish", "register"]  # no display made, so none to sleep


def test_stopping_once_there_is_a_display_puts_the_panel_to_sleep(booted):
    seen = booted()

    with pytest.raises(SystemExit) as stopped:
        seen["handlers"][signal.SIGTERM](signal.SIGTERM, None)

    assert stopped.value.code == 0
    assert seen["order"][-1] == "panel asleep"


def test_run_registers_as_a_renderer_with_the_broker_url(booted):
    registrar = booted()["registrar"]

    assert registrar.broker_url == "https://broker.example.com"


def test_run_shares_the_one_registration_between_the_target_the_broker_and_the_registry(booted):
    seen = booted()

    assert seen["target_registration"] is REGISTRATION
    assert seen["target"] == "the target"
    assert seen["broker"].registration is REGISTRATION
    assert seen["registry"].registration is REGISTRATION


def test_run_hands_the_publisher_it_started_to_the_registry_with_one_device_status(booted):
    seen = booted()

    registry = seen["registry"]
    assert registry.pub is seen["publisher"]
    assert registry.status is seen["publisher"].device_status
    assert registry.status.display is seen["display"]
    assert registry.status.last_broker_sync is not None
