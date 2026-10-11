"""The device's name: the DEVICE_NAME setting (else the hostname), as a slug. It labels the device on the broker. Not the broker's
device_id, which a renderer gets when it registers."""

import os
import socket
import string

_DEVICE_NAME_CHARS = frozenset(string.ascii_lowercase + string.digits + "_-")


def _sanitize(raw: str) -> str:
    """Lowercases and replaces anything outside [a-z0-9_-] with '-', so the name is safe
    as a metric label and a header value."""
    chars = (c.lower() if c.isascii() else c for c in raw.strip())
    return "".join(c if c in _DEVICE_NAME_CHARS else "-" for c in chars)


def resolve_device_name(configured: str | None, hostname: str | None) -> str:
    """An explicit DEVICE_NAME wins, otherwise the hostname."""
    if configured and configured.strip():
        source, raw = "DEVICE_NAME", configured
    elif hostname and hostname.strip():
        source, raw = "hostname", hostname
    else:
        raise ValueError("DEVICE_NAME is not set and the hostname could not be determined")

    device_name = _sanitize(raw)
    if all(c == "-" for c in device_name):
        raise ValueError(f"{source} {raw!r} has no usable characters for a device name")
    return device_name


def device_name_from_env() -> str:
    """DEVICE_NAME, or else the hostname."""
    return resolve_device_name(os.environ.get("DEVICE_NAME"), socket.gethostname())
