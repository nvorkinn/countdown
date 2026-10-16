from abc import abstractmethod
from typing import TypeVar

from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.spotify.models import Artist, TopResponse, Track
from countdown_core.utils.utils import UBUNTU_MEDIUM_15, truncate_to_fit

T = TypeVar("T", Artist, Track)


class AbstractSpotifyTopSubPanel(Panel):
    def __init__(self, header: str, top_response: TopResponse[T]) -> None:
        self.header = header
        self.top_response = top_response

    def render(self, image_width: int, image_height: int) -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.font = UBUNTU_MEDIUM_15
        draw.fontmode = "1"

        draw.text((0, 0), self.header, "black")
        y = 20
        for idx, row in enumerate(self.top_response.items):
            draw.text((0, y), truncate_to_fit(self.format_line(idx, row), UBUNTU_MEDIUM_15, image_width - 10), "black")
            y += 15

        return img

    @abstractmethod
    def format_line(self, idx: int, row: T) -> str:
        pass
