"""One JSON object per log line, with the field names VictoriaLogs reads: `_msg` and `level`.

The container's log driver stamps each line with its time, and the host's collector adds the service name, so
neither is here.
A traceback stays inside its record (`exception`) instead of becoming a line of its own.

The lines go to the central log store, so none may say where a screen is: no postcode, place, district or
coordinates, nor a URL with one in it. A failed request's text quotes its URL, so it's logged by its reason
(error_reason) instead, and urllib3, whose retry warnings and request lines quote the URLs it asks, is kept quiet.
"""

import json
import logging
import os
from typing import TYPE_CHECKING, override

import requests

from countdown_core.core.device_name import device_name_from_env

if TYPE_CHECKING:
    from collections.abc import Mapping

# Python's names for the two levels that differ from the shared vocabulary: debug, info, warn, error, fatal.
LEVELS = {"WARNING": "warn", "CRITICAL": "fatal"}
# Quiet at the root's level: at DEBUG each one logs every connection and frame.
NOISY = ("asyncio", "PIL", "opentelemetry")
# Off, whatever the root's level: their lines quote the URLs asked for, a postcode's, a place's or its coordinates'.
# That's urllib3's warning on each retry and its debug line for each request; it logs nothing at ERROR, and a request
# that fails for good raises, for its caller to log.
SILENCED = ("urllib3",)


class JsonFormatter(logging.Formatter):
    """`fixed` is added to every record, e.g. the device the process is."""

    def __init__(self, **fixed: str) -> None:
        """Add the `fixed` fields to every record."""
        super().__init__()
        self.fixed = fixed

    @override
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "level": LEVELS.get(record.levelname, record.levelname.lower()),
            "_msg": record.getMessage(),
            "logger": record.name,
            "source": f"{record.filename}:{record.lineno}",
            **self.fixed,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(fixed: Mapping[str, str] | None = None) -> None:
    """JSON lines on stderr, with the `fixed` fields on every line. An unrecognised LOG_LEVEL falls back to INFO.

    By default that's the `device` the process renders (DEVICE_NAME, else the hostname). A process that renders many
    screens (the render pool's) names itself instead, and each line names the screen it's about.
    """
    level = logging.getLevelNamesMapping().get(os.environ.get("LOG_LEVEL", "").upper(), logging.INFO)
    if fixed is None:
        try:
            fixed = {"device": device_name_from_env()}
        except ValueError:
            fixed = {}
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(**fixed))
    logging.basicConfig(level=level, handlers=[handler])
    for name in NOISY:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    for name in SILENCED:
        logging.getLogger(name).setLevel(logging.ERROR)


def error_reason(error: BaseException) -> str:
    """Say what went wrong without the error's own text: its type, and an HTTP error's status ("HTTPError 503").

    For a log line about anything to do with where a screen is. A failed request's text quotes its URL, with the
    postcode, place or coordinates it asked about (or an API key) in it, and a failed Redis pipeline's quotes its
    commands: keys and values.
    """
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"{type(error).__name__} {error.response.status_code}"
    return type(error).__name__


class WithheldError(Exception):
    """Stands in for an exception whose text mustn't be logged: its reason (error_reason) and its traceback."""


def without_text(error: BaseException) -> WithheldError:
    """Return `error` fit to log as exc_info: where it was raised, frame by frame, and its reason, but not its text.

    The frames show the code that raised it, not the values it had, so a traceback says where a failure was without
    saying what it was about.
    """
    return WithheldError(error_reason(error)).with_traceback(error.__traceback__)
