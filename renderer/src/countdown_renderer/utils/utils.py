"""Drawing helpers shared by the panels: fonts, sizes, borders and images."""

import functools
import http.client
import os
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GAP = 5  # between panels, and between them and the screen's edge
# Between the weather and the arrivals, whose edges would otherwise read as one block.
WEATHER_GAP = 3 * GAP
# The width the full-screen pictures (pairing code, nothing to show) are sized for.
DESIGN_WIDTH = 800

PACKAGE_DIR = Path(__file__).resolve().parent.parent
FONTS_DIR = PACKAGE_DIR / "fonts"
IMAGES_DIR = PACKAGE_DIR / "images"


def _download(url: str, attempts: int = 3) -> bytes:
    """Fetch `url`, trying again when the connection drops part-way: the 10 MB font does, now and then on GitHub."""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 -- only ever google_symbols()'s fixed https URL
                return resp.read()
        except OSError, http.client.HTTPException:  # URLError and timeouts are OSErrors; IncompleteRead is not
            if attempt == attempts:
                raise
    raise AssertionError  # pragma: no cover -- the loop returns or raises


def _cached_font(url: str, filename: str) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / filename
    if not path.exists():
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        # One file per process: test workers may all download it at once.
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.part")
        tmp.write_bytes(_download(url))
        tmp.replace(path)  # atomic, so a killed download never leaves a broken font
    return ImageFont.truetype(path)


def width_scale(image_width: int) -> float:
    """How much smaller to draw a full-screen picture sized for DESIGN_WIDTH, to fit `image_width`."""
    return min(1.0, image_width / DESIGN_WIDTH)


@functools.cache
def fitted_font(filename: str, size: int, scale: float = 1.0) -> ImageFont.FreeTypeFont:
    """Load the font in fonts/ at `size` points, times `scale`."""
    return ImageFont.truetype(str(FONTS_DIR / filename), round(size * scale))


def resized(font: ImageFont.FreeTypeFont, size: int) -> ImageFont.FreeTypeFont:
    """Return `font` at `size` points.

    The same as font.font_variant(size=size), except a size asked for again gets the same font back:
    a new one opens its file again, and its first layout costs several milliseconds more.
    """
    return _font_at(str(font.path), size)


@functools.cache
def _font_at(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


METEOCONS = ImageFont.truetype(str(FONTS_DIR / "meteocons.ttf"), 190)
JOSEFIN_REGULAR = ImageFont.truetype(str(FONTS_DIR / "JosefinSans-Regular.ttf"), 40)
JOSEFIN_SMALL = ImageFont.truetype(str(FONTS_DIR / "JosefinSans-Regular.ttf"), 28)
UBUNTU_REGULAR = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Regular.ttf"))
UBUNTU_BOLD = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Bold.ttf"), 14)
GOOGLE_REGULAR = ImageFont.truetype(str(FONTS_DIR / "GoogleSans-Regular.ttf"), 14)
GOOGLE_SEMI = ImageFont.truetype(str(FONTS_DIR / "GoogleSans-SemiBold.ttf"), 14)


@functools.cache
def google_symbols() -> ImageFont.FreeTypeFont:
    """Load Material Symbols, downloaded on first use from a pinned commit (the snapshots' icons depend on its bytes).

    Not at import, so importing countdown_shared never needs the network.
    """
    return _cached_font(
        "https://github.com/google/material-design-icons/raw/49d4db35df873165d6bd6ba09b063c7dafbac2f4/variablefont/MaterialSymbolsOutlined%5BFILL,GRAD,opsz,wght%5D.ttf",
        "MaterialSymbolsOutlined[FILL,GRAD,opsz,wght].ttf",
    )


def truncate_to_fit(text: str, font: ImageFont.BaseImageFont, max_width: float) -> str:
    """Truncate text with an ellipsis if it exceeds max_width pixels."""
    if font.getlength(text) <= max_width:
        return text

    while text and font.getlength(text + "…") > max_width:
        text = text[:-1]

    return text + "…"


def add_border(img: Image.Image) -> None:
    """Draw a 1-pixel black line round the edge of `img`."""
    draw = ImageDraw.Draw(img)
    width = img.size[0]
    height = img.size[1]
    draw.line((0, 0, width, 0), fill="black")
    draw.line((0, 0, 0, height), fill="black")
    draw.line((width - 1, height - 1, width - 1, 0), fill="black")
    draw.line((width - 1, height - 1, 0, height - 1), fill="black")


def load_tfl_roundel() -> Image.Image:
    """Load the TfL roundel, 16 pixels high and white, keeping its transparency."""
    roundel = Image.open(IMAGES_DIR / "tfl.png")
    ratio = roundel.size[0] / roundel.size[1]
    height = 16
    width = int(height * ratio)
    resized = roundel.resize((width, height), Image.Resampling.LANCZOS)
    _, _, _, alpha = resized.split()
    white_resized = Image.new("RGBA", resized.size, (255, 255, 255, 255))
    white_resized.putalpha(alpha)
    return white_resized


ROUNDEL = load_tfl_roundel()
