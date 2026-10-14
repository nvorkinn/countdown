from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.utils.utils import fitted_font, width_scale

# Letters are white on black squares; if thin white strokes fill in on the real panel,
# switch this to "Ubuntu-Medium.ttf".
LETTER_FONT = "Ubuntu-Regular.ttf"

TITLE, BODY, SMALL = 64, 36, 30

MARGIN = 40
MAX_SIDE = 104
GAP = 12


class PairingCodePanel(Panel):
    """Shown full-screen while a device isn't paired yet. has_changed says whether the code
    differs from the last check, so an unchanged screen isn't repainted."""

    def __init__(self, pairing_code: str | None, device_id: str, has_changed: bool):
        super().__init__()
        self.pairing_code = pairing_code
        self.device_id = device_id
        self.has_changed = has_changed

    def render(self, image_width: int, image_height: int) -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        center_x, center_y = image_width / 2, image_height / 2
        # Sized for 800 wide: a narrower (portrait) screen gets it all smaller.
        scale = width_scale(image_width)

        def text(offset: float, words: str, size: int) -> None:
            font = fitted_font("Ubuntu-Medium.ttf", size, scale)
            draw.text((center_x, center_y + offset * scale), words, anchor="mm", font=font, fill="black")

        text(-178, "Set up this display", TITLE)
        text(-98, "Go to nikolaivorkinn.com", BODY)
        text(-54, "and enter this code:", BODY)
        self._draw_code(draw, image_width, center_y - 4 * scale)
        text(168, f"Device: {self.device_id}", SMALL)

        return img

    def _draw_code(self, draw: ImageDraw.ImageDraw, image_width: int, top: float) -> None:
        code = self.pairing_code or ""
        if not code:
            return
        # Shrinks the squares for a code too long to fit at full size.
        side = min(MAX_SIDE, (image_width - 2 * MARGIN - GAP * (len(code) - 1)) // len(code))
        font = fitted_font(LETTER_FONT, round(78 * side / MAX_SIDE))
        x = (image_width - (len(code) * side + (len(code) - 1) * GAP)) / 2
        for char in code:
            draw.rectangle((x, top, x + side - 1, top + side - 1), fill="black")
            draw.text((x + side / 2, top + side / 2 + 2), char, anchor="mm", font=font, fill="white")
            x += side + GAP
