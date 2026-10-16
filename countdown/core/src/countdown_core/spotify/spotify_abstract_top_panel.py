"""What the two halves of the classic top tracks and artists page share."""

from abc import abstractmethod
from typing import TYPE_CHECKING, TypeVar, override

from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.spotify.models import Artist, TopResponse, Track
from countdown_core.utils.utils import UBUNTU_MEDIUM_15, truncate_to_fit

if TYPE_CHECKING:
    from countdown_core.utils.clock import ClockFormat

T = TypeVar("T", Artist, Track)


class AbstractSpotifyTopSubPanel(Panel):
    """One of the top lists in the classic style: a heading, then a numbered line per item."""

    def __init__(self, header: str, top_response: TopResponse[T]) -> None:
        """`header`: the list's heading, in English (it's translated as it's drawn); `top_response`: its items."""
        self.header = header
        self.top_response = top_response

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.font = UBUNTU_MEDIUM_15
        draw.fontmode = "1"

        draw.text((0, 0), _(self.header), "black")
        y = 20
        for idx, row in enumerate(self.top_response.items):
            draw.text((0, y), truncate_to_fit(self.format_line(idx, row), UBUNTU_MEDIUM_15, image_width - 10), "black")
            y += 15

        return img

    @abstractmethod
    def format_line(self, idx: int, row: T) -> str:
        """Write the line for `row`, the list's `idx`th item (from 0)."""
