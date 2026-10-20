"""The pairing screen: the code to enter on the website to set the device up."""

import urllib.parse
from typing import TYPE_CHECKING, override

import qrcode
from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.utils.utils import fitted_font, width_scale

if TYPE_CHECKING:
    from countdown_core.utils.clock import ClockFormat

# Letters are white on black squares; if thin white strokes fill in on the real panel,
# switch this to "Ubuntu-Medium.ttf".
LETTER_FONT = "Ubuntu-Regular.ttf"

TITLE, BODY, SMALL = 64, 36, 30

MARGIN = 40
MAX_SIDE = 104
GAP = 12

# Scanning it opens the pairing page with the code already typed in.
PAIRING_URL = "https://auth.nikolaivorkinn.com/pair?t={code}"
QR_BORDER = 2  # modules of white round it, the QR spec's quiet zone being 4 but the screen's margin adding to it
QR_SIDE = 340
QR_SIDE_TALL = 300
# On a wide screen the words and the code share the left SIDEWAYS_COLUMN pixels with the QR code on the right.
SIDEWAYS_COLUMN = 360
SIDEWAYS_TITLE, SIDEWAYS_BODY = 44, 28
# On a tall screen the words move up this far (before scaling), so there is as much white above them as under the
# QR code, which sits QR_TOP_TALL (before scaling) below the middle.
TALL_SHIFT = 238
QR_TOP_TALL = -28


class PairingCodePanel(Panel):
    """Shown full-screen while a device isn't paired yet.

    has_changed says whether the code differs from the last check, so an unchanged screen isn't repainted.
    """

    def __init__(self, pairing_code: str | None, device_id: str, *, has_changed: bool) -> None:
        """`pairing_code` is the broker's code to show (None for no code yet); `device_id` names the device."""
        super().__init__()
        self.pairing_code = pairing_code
        self.device_id = device_id
        self.has_changed = has_changed

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        # Sized for 800 wide: a narrower (portrait) screen gets it all smaller.
        scale = width_scale(image_width)
        sideways = image_width > image_height
        # Wide screen: the words and the code on the left, the QR code on the right. Tall: the QR code under them.
        column_x, column_width = (MARGIN, SIDEWAYS_COLUMN) if sideways else (0, image_width)
        center_x, center_y = column_x + column_width / 2, image_height / 2
        shift = 0 if sideways else -TALL_SHIFT

        def text(offset: float, words: str, size: int) -> None:
            font = fitted_font("Ubuntu-Medium.ttf", size, scale)
            draw.text((center_x, center_y + (offset + shift) * scale), words, anchor="mm", font=font, fill="black")

        title, body, small = (TITLE, BODY, SMALL) if not sideways else (SIDEWAYS_TITLE, SIDEWAYS_BODY, SMALL - 6)
        text(-178 if not sideways else -170, _("Set up this display"), title)
        text(-98 if not sideways else -105, _("Go to {site}").format(site="nikolaivorkinn.com"), body)
        text(-54 if not sideways else -68, _("and enter this code:"), body)
        self._draw_code(draw, column_x, column_width, center_y + (-4 + shift) * scale)
        text(168, _("Device: {device}").format(device=self.device_id), small)

        if self.pairing_code:
            if sideways:
                side = round(QR_SIDE * min(1.0, image_height / 480))
                top_left = (image_width - MARGIN - side, round((image_height - side) / 2))
            else:
                side = QR_SIDE_TALL
                top_left = (round((image_width - side) / 2), round(center_y + QR_TOP_TALL * scale))
            img.paste(self._qr_code(side), top_left)

        return img

    def _qr_code(self, side: int) -> Image.Image:
        """Draw the QR code to the pairing page, code filled in, at most `side` pixels square, centred on white."""
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=QR_BORDER, box_size=1)
        qr.add_data(PAIRING_URL.format(code=urllib.parse.quote(self.pairing_code or "")))
        qr.make(fit=True)
        modules = qr.modules_count + 2 * QR_BORDER
        # Whole pixels per module keep the squares crisp.
        box = max(1, side // modules)
        qr.box_size = box
        picture = qr.make_image(fill_color="black", back_color="white").get_image().convert("RGBA")
        canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
        canvas.paste(picture, ((side - picture.width) // 2, (side - picture.height) // 2))
        return canvas

    def _draw_code(self, draw: ImageDraw.ImageDraw, left: float, width: float, top: float) -> None:
        code = self.pairing_code or ""
        if not code:
            return
        # Shrinks the squares for a code too long to fit at full size.
        margin = 0 if left else MARGIN
        side = min(MAX_SIDE, round((width - 2 * margin - GAP * (len(code) - 1)) // len(code)))
        font = fitted_font(LETTER_FONT, round(78 * side / MAX_SIDE))
        x = left + (width - (len(code) * side + (len(code) - 1) * GAP)) / 2
        for char in code:
            draw.rectangle((x, top, x + side - 1, top + side - 1), fill="black")
            draw.text((x + side / 2, top + side / 2 + 2), char, anchor="mm", font=font, fill="white")
            x += side + GAP
