"""One JSON object per log line, with the field names VictoriaLogs reads: `_msg` and `level`. The container's
log driver stamps each line with its time, and the host's collector adds the service name, so neither is here.
A traceback stays inside its record (`exception`) instead of becoming a line of its own.
"""

import json
import logging
import os

from countdown_credentials.device_name import device_name_from_env

# Python's names for the two levels that differ from the shared vocabulary: debug, info, warn, error, fatal.
LEVELS = {"WARNING": "warn", "CRITICAL": "fatal"}
# Quiet at the root's level: at DEBUG each one logs every connection and frame.
NOISY = ("urllib3", "asyncio", "PIL", "opentelemetry")


class JsonFormatter(logging.Formatter):
    """`fixed` is added to every record, e.g. the device the process is."""

    def __init__(self, **fixed: str) -> None:
        super().__init__()
        self.fixed = fixed

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


def configure_logging() -> None:
    """JSON lines on stderr. An unrecognised LOG_LEVEL falls back to INFO."""
    level = logging.getLevelNamesMapping().get(os.environ.get("LOG_LEVEL", "").upper(), logging.INFO)
    try:
        fixed = {"device": device_name_from_env()}
    except ValueError:
        fixed = {}
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(**fixed))
    logging.basicConfig(level=level, handlers=[handler])
    for name in NOISY:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
