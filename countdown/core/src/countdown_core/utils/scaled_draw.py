"""Drawing a panel laid out for the 7.5" screens at a whole multiple of its size, crisply.

A panel's code keeps its own pixels (and measures text with its own fonts); ScaledDraw multiplies every position and
every font as it draws, so the text is set at the bigger size rather than its pixels repeated.
"""

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from countdown_core.utils.utils import resized

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import ImageFont

Point = tuple[float, float]
# A colour, as the panels give PIL one: a name or a tuple.
Fill = str | tuple[int, ...] | None


def scaled_canvas(width: int, height: int, scale: int) -> tuple[Image.Image, ScaledDraw]:
    """Make a transparent `width` x `height` canvas, `scale` times as big, and what draws on it in those sizes."""
    img = Image.new("RGBA", (width * scale, height * scale), (255, 255, 255, 0))
    return img, ScaledDraw(img, scale)


class ScaledDraw:
    """ImageDraw's shapes and text, in a panel's own pixels, drawn `scale` times as big.

    A box covers the pixels its corners' pixels grow to, and a line runs through the middle of them, as if the small
    drawing had its pixels repeated; text and curves are drawn at the bigger size, so they keep smooth edges.
    """

    def __init__(self, img: Image.Image, scale: int) -> None:
        """Draw on `img`, which is already `scale` times the panel's size."""
        self.scale = scale
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

    def line(self, xy: Sequence[float], fill: Fill = None, width: int = 1) -> None:
        """Draw a line from (x0, y0) to (x1, y1), `width` pixels wide."""
        x0, y0, x1, y1 = xy
        self._draw.line((*self._at((x0, y0)), *self._at((x1, y1))), fill, width * self.scale)

    def point(self, xy: Point, fill: Fill = None) -> None:
        """Fill the pixel at `xy`, a `scale`-wide square."""
        self.rectangle((*xy, *xy), fill=fill)
