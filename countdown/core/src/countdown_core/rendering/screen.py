"""The canvas a layout draws on, and how a screen in the config maps to one."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from PIL import Image

    from countdown_api.models import Screen

Orientation = Literal["horizontal", "vertical"]


@dataclass(frozen=True)
class Canvas:
    """What a layout draws on, upright as the viewer sees it.

    Layouts are registered by this alone: a portrait layout is just a 480x800 one.
    """

    width: int
    height: int

    @property
    def size(self) -> tuple[int, int]:
        """(width, height), as PIL takes a size."""
        return self.width, self.height

    def __str__(self) -> str:
        """Width x height, as in 800x480."""
        return f"{self.width}x{self.height}"


# The default: an 800x480 panel hung landscape.
DEFAULT_CANVAS = Canvas(800, 480)


def canvas_for(screen: Screen) -> Canvas:
    """Find the canvas for the screen in the config.

    The device always reports the panel's size the same way round, however it's hung, so the orientation decides
    which side is long.
    """
    long, short = max(screen.width, screen.height), min(screen.width, screen.height)
    return Canvas(long, short) if screen.orientation == "horizontal" else Canvas(short, long)


def native_size(screen: Screen) -> tuple[int, int]:
    """Give the size of the panel's own picture.

    It's landscape, as the e-paper's buffer (and the broker's frame) is laid out, however the screen is hung.
    """
    return max(screen.width, screen.height), min(screen.width, screen.height)


def panel_bytes(img: Image.Image) -> bytearray:
    """Convert the panel's own picture (native_size) to its raw 1-bit buffer: the frame the screen takes.

    The bits are inverted: in the PIL world 0=black and 1=white, on the e-paper 0=white and 1=black.
    """
    buf = bytearray(img.convert("1").tobytes("raw"))
    for i in range(len(buf)):
        buf[i] ^= 0xFF
    return buf
