"""The pairing screen: the code to enter on the website to set the device up."""

import os
import urllib.parse
from typing import TYPE_CHECKING, override

import qrcode
from PIL import Image, ImageDraw

from countdown_core.i18n import _
from countdown_renderer.panel import Panel
from countdown_renderer.utils.scaled_draw import drawing_scale
from countdown_renderer.utils.utils import fitted_font, width_scale

if TYPE_CHECKING:
    from countdown_renderer.utils.clock import ClockFormat

# Letters are white on black squares; if thin white strokes fill in on the real panel,
# switch this to "Ubuntu-Medium.ttf".
LETTER_FONT = "Ubuntu-Regular.ttf"

TITLE, BODY, SMALL = 64, 36, 30

MARGIN = 40
MAX_SIDE = 104
GAP = 12

# Scanning it opens the pairing page with the code already typed in. The host is the environment's: auth_host() reads
# COUNTDOWN_AUTH_HOST when the screen is drawn, and the words name the site the same way (COUNTDOWN_SITE_NAME), so
# dev/QA and production can each point at their own. Unset or empty, they are production's.
DEFAULT_AUTH_HOST = "auth.nikolaivorkinn.com"
DEFAULT_SITE_NAME = "nikolaivorkinn.com"
PAIRING_URL = "https://{host}/pair?code={code}"
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


def auth_host() -> str:
    """Return the pairing page's host (`COUNTDOWN_AUTH_HOST`), read now rather than at import."""
    return os.environ.get("COUNTDOWN_AUTH_HOST", "").strip() or DEFAULT_AUTH_HOST


def site_name() -> str:
    """Return the website's name the screen says to go to (`COUNTDOWN_SITE_NAME`), read now, not at import."""
    return os.environ.get("COUNTDOWN_SITE_NAME", "").strip() or DEFAULT_SITE_NAME


class PairingCodePanel(Panel):
    """Shown full-screen while a device isn't paired yet.

    Laid out in the 7.5" screens' pixels, and drawn `drawing_scale()` times as big on the HD screens (see render).
    """

    scales = True

    def __init__(self, pairing_code: str, device_id: str) -> None:
        """`pairing_code` is the broker's code to show; `device_id` names the device."""
        super().__init__()
        self.pairing_code = pairing_code
        self.device_id = device_id

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        zoom = drawing_scale()
        img = Image.new("RGBA", (round(image_width * zoom), round(image_height * zoom)), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        # Sized for 800 wide: a narrower (portrait) screen gets it all smaller.
        scale = width_scale(image_width) * zoom
        sideways = image_width > image_height
        # Wide screen: the words and the code on the left, the QR code on the right. Tall: the QR code under them.
        column_x, column_width = (MARGIN, SIDEWAYS_COLUMN) if sideways else (0, image_width)
        column_x, column_width = column_x * zoom, column_width * zoom
        center_x, center_y = column_x + column_width / 2, image_height * zoom / 2
        shift = 0 if sideways else -TALL_SHIFT

        def text(offset: float, words: str, size: int) -> None:
            font = fitted_font("Ubuntu-Medium.ttf", size, scale)
            draw.text((center_x, center_y + (offset + shift) * scale), words, anchor="mm", font=font, fill="black")

        title, body, small = (TITLE, BODY, SMALL) if not sideways else (SIDEWAYS_TITLE, SIDEWAYS_BODY, SMALL - 6)
        text(-178 if not sideways else -170, _("Set up this display"), title)
        text(-98 if not sideways else -105, _("Go to {site}").format(site=site_name()), body)
        text(-54 if not sideways else -68, _("and enter this code:"), body)
        self._draw_code(draw, column_x, column_width, center_y + (-4 + shift) * scale, zoom)
        text(168, _("Device: {device}").format(device=self.device_id), small)

        if sideways:
            side = round(QR_SIDE * min(1.0, image_height / 480) * zoom)
            top_left = (round((image_width - MARGIN) * zoom - side), round((img.height - side) / 2))
        else:
            side = round(QR_SIDE_TALL * zoom)
            top_left = (round((img.width - side) / 2), round(center_y + QR_TOP_TALL * scale))
        img.paste(self._qr_code(side), top_left)

        return img

    def _qr_code(self, side: int) -> Image.Image:
        """Draw the QR code to the pairing page, code filled in, at most `side` pixels square, centred on white."""
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=QR_BORDER, box_size=1)
        qr.add_data(PAIRING_URL.format(host=auth_host(), code=urllib.parse.quote(self.pairing_code)))
        qr.make(fit=True)
        modules = qr.modules_count + 2 * QR_BORDER
        # Whole pixels per module keep the squares crisp.
        box = max(1, side // modules)
        qr.box_size = box
        picture = qr.make_image(fill_color="black", back_color="white").get_image().convert("RGBA")
        canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
        canvas.paste(picture, ((side - picture.width) // 2, (side - picture.height) // 2))
        return canvas

    def _draw_code(self, draw: ImageDraw.ImageDraw, left: float, width: float, top: float, zoom: float) -> None:
        code = self.pairing_code
        # Shrinks the squares for a code too long to fit at full size.
        margin = 0 if left else MARGIN * zoom
        gap, max_side = GAP * zoom, MAX_SIDE * zoom
        side = min(round(max_side), round((width - 2 * margin - gap * (len(code) - 1)) // len(code)))
        font = fitted_font(LETTER_FONT, round(78 * zoom * side / max_side))
        x = left + (width - (len(code) * side + (len(code) - 1) * gap)) / 2
        for char in code:
            draw.rectangle((x, top, x + side - 1, top + side - 1), fill="black")
            draw.text((x + side / 2, top + side / 2 + 2 * zoom), char, anchor="mm", font=font, fill="white")
            x += side + gap
