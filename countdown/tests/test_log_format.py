"""The JSON log lines the renderer and the orchestrator write for VictoriaLogs.

They have `_msg` and `level`, and one record per exception. Both packages carry the same formatter, since the
orchestrator's image installs only its own.
"""

import json
import logging
import sys

import pytest

from countdown_core.core import log_format as renderer
from countdown_orchestrator import log_format as orchestrator

FORMATTERS = [
    pytest.param(renderer.JsonFormatter(), id="renderer"),
    pytest.param(orchestrator.JsonFormatter(), id="orchestrator"),
]


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


@pytest.mark.parametrize("module", [renderer, orchestrator])
def test_third_party_loggers_stay_quiet_even_when_the_app_is_at_debug(module, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setattr(module.logging, "basicConfig", lambda **kwargs: None)

    module.configure_logging()

    assert logging.getLogger("urllib3").level == logging.WARNING
