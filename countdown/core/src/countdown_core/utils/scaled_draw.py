"""Drawing a panel laid out for the 7.5" screens at a whole multiple of its size, crisply.

A panel's code keeps its own pixels (and measures text with its own fonts); ScaledDraw multiplies every position and
every font as it draws, so the text is set at the bigger size rather than its pixels repeated.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from countdown_core.utils.utils import resized

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from PIL import ImageFont

Point = tuple[float, float]
# A colour, as the panels give PIL one: a name or a tuple.
Fill = str | tuple[int, ...] | None


# How many times its own size a panel that `scales` is drawn: set by the layout around its render (drawing_at).
_SCALE: ContextVar[int] = ContextVar("drawing_scale", default=1)


@contextmanager
def drawing_at(scale: int) -> Generator[None]:
    """Draw the panels rendered inside at `scale` times their size (those whose class `scales`)."""
    token = _SCALE.set(scale)
    try:
        yield
    finally:
        _SCALE.reset(token)


def drawing_scale() -> int:
    """How many times its own size a panel is being drawn: 1 but inside drawing_at()."""
    return _SCALE.get()


def scaled_canvas(
    width: int, height: int, background: tuple[int, int, int, int] = (255, 255, 255, 0)
) -> tuple[Image.Image, ScaledDraw]:
    """Make a `width` x `height` canvas at the drawing scale, and what draws on it in those sizes."""
    scale = drawing_scale()
    img = Image.new("RGBA", (width * scale, height * scale), background)
    return img, ScaledDraw(img, scale)


class ScaledDraw:
    """ImageDraw's shapes and text, in a panel's own pixels, drawn `scale` times as big.

    A box covers the pixels its corners' pixels grow to, and a line runs through the middle of them, as if the small
    drawing had its pixels repeated; text and curves are drawn at the bigger size, so they keep smooth edges.
    """

    def __init__(self, img: Image.Image, scale: int) -> None:
        """Draw on `img`, which is already `scale` times the panel's size."""
        self.scale = scale
        self._img = img
        self._draw = ImageDraw.Draw(img)
        self._draw.fontmode = "1"

    def _at(self, point: Point) -> Point:
        """Where `point`'s pixel's middle goes."""
        offset = (self.scale - 1) / 2
        return point[0] * self.scale + offset, point[1] * self.scale + offset

    def _box(self, box: Sequence[float]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = box
        s = self.scale
        return x0 * s, y0 * s, x1 * s + s - 1, y1 * s + s - 1

    def font(self, font: ImageFont.FreeTypeFont) -> ImageFont.FreeTypeFont:
        """Return `font` at `scale` times its size."""
        return resized(font, round(font.size * self.scale))

    def text(
        self,
        xy: Point,
        text: str,
        fill: Fill = None,
        font: ImageFont.FreeTypeFont | None = None,
        anchor: str | None = None,
    ) -> None:
        """Draw `text` with its anchor at `xy`, in `font` at the bigger size."""
        if font is None:
            msg = "ScaledDraw.text needs a font: the default one can't be resized"
            raise ValueError(msg)
        self._draw.text((xy[0] * self.scale, xy[1] * self.scale), text, fill, self.font(font), anchor=anchor)

    def rectangle(self, box: Sequence[float], fill: Fill = None, outline: Fill = None, width: int = 1) -> None:
        """Draw a rectangle, its outline `width` pixels wide."""
        self._draw.rectangle(self._box(box), fill, outline, width * self.scale)

    def rounded_rectangle(
        self,
        box: Sequence[float],
        radius: float = 0,
        fill: Fill = None,
        outline: Fill = None,
        width: int = 1,
    ) -> None:
        """Draw a rectangle with round corners, `radius` in the panel's pixels."""
        self._draw.rounded_rectangle(self._box(box), radius * self.scale, fill, outline, width * self.scale)

    def ellipse(self, box: Sequence[float], fill: Fill = None, outline: Fill = None, width: int = 1) -> None:
        """Draw an ellipse in `box`."""
        self._draw.ellipse(self._box(box), fill, outline, width * self.scale)

    def line(self, xy: Sequence[float] | Sequence[Point], fill: Fill = None, width: int = 1) -> None:
        """Draw a line through the points: (x0, y0, x1, y1), or a list of (x, y); `width` pixels wide."""
        points = list(zip(xy[::2], xy[1::2], strict=True)) if not isinstance(xy[0], tuple) else list(xy)
        self._draw.line([self._at(point) for point in points], fill, width * self.scale)  # ty: ignore[invalid-argument-type] -- the points are pairs either way

    def point(self, xy: Point, fill: Fill = None) -> None:
        """Fill the pixel at `xy`, a `scale`-wide square."""
        self.rectangle((*xy, *xy), fill=fill)

    def paste(self, im: Image.Image, xy: tuple[int, int]) -> None:
        """Paste `im` (already drawn at the bigger size) with its top left at `xy`, through its own transparency."""
        self._img.paste(im, (xy[0] * self.scale, xy[1] * self.scale), im)
