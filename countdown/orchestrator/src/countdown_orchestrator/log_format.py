"""One JSON object per log line, with the field names VictoriaLogs reads: `_msg` and `level`.

The container's log driver stamps each line with its time, and the host's collector adds the service name, so
neither is here.
Matches the renderer's (countdown_core.core.log_format): the orchestrator's image installs only this package.
"""

import json
import logging
import os
from typing import override

# Python's names for the two levels that differ from the shared vocabulary: debug, info, warn, error, fatal.
LEVELS = {"WARNING": "warn", "CRITICAL": "fatal"}


class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON line, with its traceback (if any) as a field rather than more lines."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "level": LEVELS.get(record.levelname, record.levelname.lower()),
            "_msg": record.getMessage(),
            "logger": record.name,
            "source": f"{record.filename}:{record.lineno}",
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    """JSON lines on stderr. An unrecognised LOG_LEVEL falls back to INFO."""
    level = logging.getLevelNamesMapping().get(os.environ.get("LOG_LEVEL", "").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler])
    for name in ("urllib3", "docker"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
