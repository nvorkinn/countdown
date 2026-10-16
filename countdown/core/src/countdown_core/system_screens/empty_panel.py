from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.utils.utils import fitted_font, width_scale

TITLE, INSTRUCTION, HINT = 72, 42, 34


class EmptyPanel(Panel):
    """Shown full-screen when the device is set up but has nothing to display yet: the first
    data hasn't arrived, or every API is failing."""

    def render(self, image_width: int, image_height: int) -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        center_x, center_y = image_width / 2, image_height / 2
        # Sized for 800 wide: a narrower (portrait) screen gets it all smaller.
        scale = width_scale(image_width)

        lines = [
            (-110, _("Nothing to show yet"), TITLE),
            (-25, _("Waiting for your first bus times"), INSTRUCTION),
            (30, _("and weather to arrive"), INSTRUCTION),
            (110, "(if it stays like this, check the Wi-Fi)", HINT),
        ]
        for offset, text, size in lines:
            font = fitted_font("Ubuntu-Medium.ttf", size, scale)
            draw.text((center_x, center_y + offset * scale), text, anchor="mm", font=font, fill="black")
        return img
