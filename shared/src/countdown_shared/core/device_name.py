"""The process's name: the DEVICE_NAME setting (else the hostname), as a slug.

It's the `device` field on every log line of a process that doesn't name itself otherwise (core.log_format: the
providers; the render pool names its processes with a `worker` field). Not the broker's device id, nor a screen's slug.
"""

import os
import socket
import string

_DEVICE_NAME_CHARS = frozenset(string.ascii_lowercase + string.digits + "_-")


def _sanitize(raw: str) -> str:
    """Lowercase `raw` and replace anything outside [a-z0-9_-] with '-'.

    So the name is safe as a metric label.
    """
    chars = (c.lower() if c.isascii() else c for c in raw.strip())
    return "".join(c if c in _DEVICE_NAME_CHARS else "-" for c in chars)


def resolve_device_name(configured: str | None, hostname: str | None) -> str:
    """Pick the device's name: an explicit DEVICE_NAME wins, otherwise the hostname."""
    if configured and configured.strip():
        source, raw = "DEVICE_NAME", configured
    elif hostname and hostname.strip():
        source, raw = "hostname", hostname
    else:
        msg = "DEVICE_NAME is not set and the hostname could not be determined"
        raise ValueError(msg)

    device_name = _sanitize(raw)
    if all(c == "-" for c in device_name):
        msg = f"{source} {raw!r} has no usable characters for a device name"
        raise ValueError(msg)
    return device_name


def device_name_from_env() -> str:
    """DEVICE_NAME, or else the hostname."""
    return resolve_device_name(os.environ.get("DEVICE_NAME"), socket.gethostname())
