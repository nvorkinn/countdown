"""How the panel's picture is encoded into the bytes a screen takes, by the screen's `format`."""

import io
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

    from countdown_api.models import Screen

Encoder = Callable[["Image.Image"], bytes]


def _mono_1bpp_packed(img: Image.Image) -> bytes:
    """Encode the picture as the e-paper's raw 1-bit buffer: the frame the screen takes.

    The bits are inverted: in the PIL world 0=black and 1=white, on the e-paper 0=white and 1=black.
    """
    return bytes(b ^ 0xFF for b in img.convert("1").tobytes("raw"))


def _png(img: Image.Image) -> bytes:
    """Encode the picture as a PNG image."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# Keyed by Screen.format; every format the API allows has one.
ENCODERS: dict[str, Encoder] = {
    "mono-1bpp-packed": _mono_1bpp_packed,
    "png": _png,
}


def encode(img: Image.Image, screen: Screen) -> bytes:
    """Encode the panel's own picture (screen.native_size) in the format the screen takes."""
    return ENCODERS[screen.format](img)
