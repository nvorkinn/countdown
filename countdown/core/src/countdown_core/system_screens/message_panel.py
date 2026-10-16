"""A short message drawn in place of a panel that has nothing to show."""

from math import floor
from typing import TYPE_CHECKING, override

from PIL import Image, ImageDraw, ImageFont

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.utils.utils import FONTS_DIR, IMAGES_DIR, UBUNTU_BOLD

if TYPE_CHECKING:
    from countdown_core.utils.clock import ClockFormat

TITLE = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Medium.ttf"), 28)


class MessagePanel(Panel):
    """Stands in for a panel with nothing to draw, so its area is never blank.

    Shows `message` with `logo` (a file in images/) if given, else `title`. Both are English, marked with N_(), and
    drawn in the screen's language.
    """

    def __init__(self, title: str, message: str, logo: str | None = None) -> None:
        """`title` is the panel's name and `message` what to say; `logo`, a file in images/, takes the title's place."""
        self.title = title
        self.message = message
        self.logo = logo

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"

        if self.logo:
            draw.text((image_width / 2, 5), _(self.message), "black", font=UBUNTU_BOLD, anchor="ma")
            logo = Image.open(IMAGES_DIR / self.logo)
            logo.thumbnail((image_width - 10, image_height - 30))
            img.paste(logo, (floor((image_width - logo.size[0]) / 2), 25), logo)
        else:
            center_x, center_y = image_width / 2, image_height / 2
            draw.text((center_x, center_y - 12), _(self.title), "black", font=TITLE, anchor="mm")
            draw.text((center_x, center_y + 16), _(self.message), "black", font=UBUNTU_BOLD, anchor="mm")

        return img
