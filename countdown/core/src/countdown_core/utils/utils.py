import functools
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GAP = 5  # between panels, and between them and the screen's edge
# The width the full-screen pictures (pairing code, nothing to show) are sized for.
DESIGN_WIDTH = 800

PACKAGE_DIR = Path(__file__).resolve().parent.parent
FONTS_DIR = PACKAGE_DIR / "fonts"
IMAGES_DIR = PACKAGE_DIR / "images"


def _cached_font(url: str, filename: str) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / filename
    if not path.exists():
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=10) as resp:
            tmp.write_bytes(resp.read())
        tmp.replace(path)  # atomic, so a killed download never leaves a broken font
    return ImageFont.truetype(path)


def width_scale(image_width: int) -> float:
    """How much smaller to draw a full-screen picture sized for DESIGN_WIDTH, to fit `image_width`."""
    return min(1.0, image_width / DESIGN_WIDTH)


@functools.cache
def fitted_font(filename: str, size: int, scale: float = 1.0) -> ImageFont.FreeTypeFont:
    """The font in fonts/ at `size` points, times `scale`."""
    return ImageFont.truetype(str(FONTS_DIR / filename), round(size * scale))


METEOCONS = ImageFont.truetype(str(FONTS_DIR / "meteocons.ttf"), 190)
JOSEFIN_REGULAR = ImageFont.truetype(str(FONTS_DIR / "JosefinSans-Regular.ttf"), 40)
JOSEFIN_SMALL = ImageFont.truetype(str(FONTS_DIR / "JosefinSans-Regular.ttf"), 28)
UBUNTU_REGULAR = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Regular.ttf"))
UBUNTU_MEDIUM = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Medium.ttf"), 14)
UBUNTU_MEDIUM_20 = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Medium.ttf"), 20)
UBUNTU_MEDIUM_15 = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Medium.ttf"), 15)
UBUNTU_BOLD = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Bold.ttf"), 14)
UBUNTU_CONDENSED = ImageFont.truetype(str(FONTS_DIR / "UbuntuCondensed-Regular.ttf"), 14)
UBUNTU_BOLD_20 = ImageFont.truetype(str(FONTS_DIR / "Ubuntu-Bold.ttf"), 20)
GOOGLE_REGULAR = ImageFont.truetype(str(FONTS_DIR / "GoogleSans-Regular.ttf"), 14)
GOOGLE_SEMI = ImageFont.truetype(str(FONTS_DIR / "GoogleSans-SemiBold.ttf"), 14)


@functools.cache
def google_symbols() -> ImageFont.FreeTypeFont:
    """Material Symbols, downloaded on first use rather than at import, so importing
    countdown_core (the app at boot, every test) never needs the network."""
    return _cached_font(
        "https://github.com/google/material-design-icons/raw/refs/heads/master/variablefont/MaterialSymbolsOutlined%5BFILL,GRAD,opsz,wght%5D.ttf",
        "MaterialSymbolsOutlined[FILL,GRAD,opsz,wght].ttf",
    )


def add_border(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    width = img.size[0]
    height = img.size[1]
    draw.line((0, 0, width, 0), fill="black")
    draw.line((0, 0, 0, height), fill="black")
    draw.line((width - 1, height - 1, width - 1, 0), fill="black")
    draw.line((width - 1, height - 1, 0, height - 1), fill="black")


def load_tfl_roundel() -> Image.Image:
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
