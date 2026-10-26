"""The JSON log lines the render pool and the providers write for VictoriaLogs.

They have `_msg` and `level`, and one record per exception.
"""

import json
import logging
import sys

import pytest
import requests

from countdown_core.core import log_format as renderer

FORMATTERS = [pytest.param(renderer.JsonFormatter(), id="renderer")]


def record(level=logging.INFO, msg="hello %s", args=("world",), exc_info=None):
    return logging.LogRecord("countdown.test", level, "/src/module.py", 42, msg, args, exc_info)


@pytest.mark.parametrize("formatter", FORMATTERS)
def test_a_line_is_one_json_object_with_the_fields_victorialogs_reads(formatter):
    entry = json.loads(formatter.format(record()))

    assert entry == {"level": "info", "_msg": "hello world", "logger": "countdown.test", "source": "module.py:42"}


@pytest.mark.parametrize("formatter", FORMATTERS)
@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (logging.DEBUG, "debug"),
        (logging.INFO, "info"),
        (logging.WARNING, "warn"),
        (logging.ERROR, "error"),
        (logging.CRITICAL, "fatal"),
    ],
)
def test_levels_use_the_shared_vocabulary(formatter, level, expected):
    assert json.loads(formatter.format(record(level)))["level"] == expected


def fail() -> None:
    msg = "boom"
    raise ValueError(msg)


@pytest.mark.parametrize("formatter", FORMATTERS)
def test_a_traceback_stays_inside_its_record(formatter):
    try:
        fail()
    except ValueError:
        line = formatter.format(record(logging.ERROR, "failed", (), sys.exc_info()))

    assert "\n" not in line  # one line for the log driver, so one record in VictoriaLogs
    entry = json.loads(line)
    assert entry["_msg"] == "failed"
    assert "Traceback" in entry["exception"]
    assert "ValueError: boom" in entry["exception"]


def test_the_renderer_adds_the_device_it_is_to_every_line():
    entry = json.loads(renderer.JsonFormatter(device="kitchen").format(record()))

    assert entry["device"] == "kitchen"


def test_the_renderer_knows_its_device_from_the_environment(monkeypatch):
    monkeypatch.setenv("DEVICE_NAME", "Kitchen")
    handlers = []
    monkeypatch.setattr(renderer.logging, "basicConfig", lambda **kwargs: handlers.extend(kwargs["handlers"]))

    renderer.configure_logging()

    assert json.loads(handlers[0].formatter.format(record()))["device"] == "kitchen"


def test_a_process_drawing_many_screens_names_itself_instead_of_a_device(monkeypatch):
    """The render pool's worker: each line names the screen it's about, and `device` would name the host."""
    monkeypatch.setenv("DEVICE_NAME", "Kitchen")
    handlers = []
    monkeypatch.setattr(renderer.logging, "basicConfig", lambda **kwargs: handlers.extend(kwargs["handlers"]))

    renderer.configure_logging({"worker": "render-worker-1"})

    entry = json.loads(handlers[0].formatter.format(record()))
    assert entry["worker"] == "render-worker-1"
    assert "device" not in entry


@pytest.fixture
def levels_kept():
    """Put the third-party loggers' levels back after a test that configures the logging."""
    loggers = [logging.getLogger(name) for name in ("urllib3", "asyncio", "PIL", "opentelemetry")]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


@pytest.mark.usefixtures("levels_kept")
def test_third_party_loggers_stay_quiet_even_when_the_app_is_at_debug(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setattr(renderer.logging, "basicConfig", lambda **kwargs: None)

    renderer.configure_logging()

    assert logging.getLogger("asyncio").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.ERROR


@pytest.mark.usefixtures("levels_kept")
@pytest.mark.parametrize(
    ("env", "expected"), [(None, logging.INFO), ("debug", logging.DEBUG), ("nonsense", logging.INFO)]
)
def test_logging_defaults_to_info_and_honours_log_level(monkeypatch, env, expected):
    calls = []
    monkeypatch.setattr(renderer.logging, "basicConfig", lambda **kwargs: calls.append(kwargs))
    if env is None:
        monkeypatch.delenv("LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("LOG_LEVEL", env)

    renderer.configure_logging()

    (kwargs,) = calls
    assert kwargs["level"] == expected
    (handler,) = kwargs["handlers"]
    assert isinstance(handler.formatter, renderer.JsonFormatter)  # the container's log driver timestamps each line


@pytest.mark.usefixtures("levels_kept")
def test_the_renderer_and_providers_keep_urllib3_quiet_as_it_quotes_the_urls_it_asks(monkeypatch):
    """Its retry warnings and request lines quote the URL: a postcode's, a place's, its coordinates'."""
    monkeypatch.setattr(renderer.logging, "basicConfig", lambda **kwargs: None)

    renderer.configure_logging()

    assert not logging.getLogger("urllib3.connectionpool").isEnabledFor(logging.WARNING)


class FakeResponse:
    status_code = 503


def test_an_errors_reason_is_its_type_and_an_http_errors_status_not_its_text():
    url = "https://api.postcodes.io/postcodes/SE17%203LL"
    assert renderer.error_reason(requests.ConnectionError(f"Max retries exceeded with url: {url}")) == "ConnectionError"
    http_error = requests.HTTPError(f"503 Server Error for url: {url}", response=FakeResponse())
    assert renderer.error_reason(http_error) == "HTTPError 503"
    assert renderer.error_reason(requests.HTTPError("no response")) == "HTTPError"


def test_an_error_without_its_text_keeps_its_traceback_for_the_log(caplog):
    try:
        fail()
    except ValueError as e:
        logging.getLogger("countdown.test").error("failed", exc_info=renderer.without_text(e))  # noqa: TRY400

    exception = json.loads(renderer.JsonFormatter().format(caplog.records[0]))["exception"]
    assert "in fail\n    raise ValueError(msg)" in exception  # where it was raised
    assert exception.endswith("WithheldError: ValueError")
    assert "boom" not in exception
