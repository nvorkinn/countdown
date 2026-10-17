"""Where DisplayController's frames end up.

The controller decides what to paint and when; a target only knows how to get one picture onto one kind of screen.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

Region = tuple[int, int, int, int]


class DisplayTarget(ABC):
    """One kind of screen the frames can be painted on."""

    # Whether the screen answered; None until known (or for targets that can't tell).
    connected: bool | None = None

    def set_pace(self, seconds: int | None) -> None:  # noqa: B027 -- optional hook: only a target with a broker needs it
        """Tell the target how fast the providers want the screen to refresh now, or None for the usual pace.

        Called before each cycle's paint. A target whose screen is paced elsewhere (the broker's `Retry-After`)
        passes it on with the next frame; the others ignore it.
        """

    def renew(self) -> None:  # noqa: B027 -- optional hook: only a target with a broker needs it
        """Tell the screen's pace again when the picture hasn't changed, so what was asked for isn't allowed to lapse.

        Called instead of a paint whenever the picture on the screen is already the right one.
        """

    @abstractmethod
    def paint(self, img: Image.Image, region: Region | None = None) -> bool:
        """Put the full 800x480 `img` on the screen, or with a region just that part (a partial refresh).

        The region is byte-aligned. False if there's no screen to take it.
        """

    def close(self) -> None:  # noqa: B027 -- optional hook: only a target holding hardware needs it
        """Release the screen. Run at shutdown, from inside the SIGTERM handler, so it mustn't raise."""


def panel_bytes(img: Image.Image, region: Region | None = None) -> bytearray:
    """Convert the picture (or the region of it) to the panel's raw 1-bit buffer.

    The bytes need inverting: in the PIL world 0=black and 1=white, on the e-paper 0=white and 1=black.
    """
    if region is not None:
        img = img.crop(region)
    buf = bytearray(img.convert("1").tobytes("raw"))
    for i in range(len(buf)):
        buf[i] ^= 0xFF
    return buf
