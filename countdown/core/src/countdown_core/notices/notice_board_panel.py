"""How the notice board is drawn."""

from typing import TYPE_CHECKING, ClassVar, Self, override

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_styles import STYLES
from countdown_core.utils.clock import ClockFormat, format_clock
from countdown_core.utils.scaled_draw import ScaledDraw, scaled_canvas
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, resized, truncate_to_fit

if TYPE_CHECKING:
    from PIL import Image, ImageFont

    from countdown_core.core.style import Style

SOURCE_FONT = GOOGLE_SEMI
TEXT_FONT = GOOGLE_REGULAR

ROW_HEIGHT = 22
MARKER_SIZE = 14  # the square severity marker at the start of each row
MARKER_GAP = 6  # between the marker and the source tag
SOURCE_GAP = 6  # between the source tag and the text


class NoticeBoardPanel(Panel):
    """The notice board: its style's title and frame, then a row per notice, the most severe first."""

    scales = True

    # The rows: how tall, in which fonts, and whether the text column starts where the widest source shown ends
    # (a table) or after each row's own source.
    row_height: ClassVar[int] = ROW_HEIGHT
    source_font: ClassVar[ImageFont.FreeTypeFont] = SOURCE_FONT
    text_font: ClassVar[ImageFont.FreeTypeFont] = TEXT_FONT
    aligned: ClassVar[bool] = False

    def __init__(self, notices: list[Notice], style: Style = "hero") -> None:
        """`notices` already ranked, most important first; `style`: how to draw the board round them."""
        self.notices = notices
        self.style = style

    @override
    def fitted_height(self, max_height: int) -> int:
        """Leave room for a row per notice, or nothing at all when there are none.

        At `max_height` the rows that don't fit are counted on the last one.
        """
        if not self.notices:
            return 0
        style = STYLES[self.style]
        return min(max_height, style.top + len(self.notices) * self.row_height + style.bottom)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        """Draw the style's chrome, then one notice per row: a severity marker, the source and as much text as fits.

        If there are more current notices than rows, the last row says how many didn't fit.
        """
        style = STYLES[self.style]
        img, draw = scaled_canvas(image_width, image_height, (255, 255, 255, 255))
        style.draw(draw, image_width, image_height, len(self.notices))

        left, right = style.inset, image_width - style.inset
        rows = max(1, (image_height - style.top - style.bottom) // self.row_height)
        current = [n for n in self.notices if n.severity != Severity.PLANNED]
        planned = [n for n in self.notices if n.severity == Severity.PLANNED]
        if len(current) > rows:
            shown, more = current[: rows - 1], len(current) - (rows - 1)
        else:
            # Planned works only fill the rows left over, and don't count as "more".
            shown, more = current + planned[: rows - len(current)], 0
        text_x = None
        if self.aligned and shown:
            widest = max(round(self.source_font.getlength(notice.source)) for notice in shown)
            text_x = left + MARKER_SIZE + MARKER_GAP + widest + SOURCE_GAP
        for i, notice in enumerate(shown):
            self._draw_row(draw, notice, style.top + i * self.row_height, (left, right), clock_format, text_x)
        if more:
            y = style.top + len(shown) * self.row_height + self.row_height // 2
            draw.text((left, y), _("+{count} more").format(count=more), "black", font=self.text_font, anchor="lm")
        return img

    def _draw_row(  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, and how
        self,
        draw: ScaledDraw,
        notice: Notice,
        y: int,
        span: tuple[int, int],
        clock_format: ClockFormat,
        text_x: int | None = None,
    ) -> None:
        """Draw `notice`'s row with its top at `y`, between the x coordinates `span`; its text from `text_x`, if set."""
        left, right = span
        middle = y + self.row_height // 2
        box = (left, middle - MARKER_SIZE // 2, left + MARKER_SIZE - 1, middle + MARKER_SIZE // 2 - 1)
        centre = ((box[0] + box[2] + 1) / 2, middle)
        if notice.severity == Severity.SEVERE:
            draw.rectangle(box, fill="black")
            draw.text(centre, "!", "white", font=self.source_font, anchor="mm")
        elif notice.severity == Severity.WARNING:
            draw.rectangle(box, outline="black", width=2)
            draw.text(centre, "!", "black", font=self.source_font, anchor="mm")
        elif notice.severity == Severity.INFO:
            r = 3
            draw.ellipse((centre[0] - r, middle - r, centre[0] + r - 1, middle + r - 1), fill="black")
        elif notice.severity == Severity.REMINDER:  # a small square
            r = 3
            draw.rectangle((centre[0] - r, middle - r, centre[0] + r - 1, middle + r - 1), fill="black")
        else:  # planned: a hollow dot
            r = 3
            draw.ellipse((centre[0] - r, middle - r, centre[0] + r - 1, middle + r - 1), outline="black")

        x = left + MARKER_SIZE + MARKER_GAP
        draw.text((x, middle), notice.source, "black", font=self.source_font, anchor="lm")
        x = text_x if text_x is not None else x + round(self.source_font.getlength(notice.source)) + SOURCE_GAP
        available = right - x
        suffix = ""
        if notice.display_time is not None:
            suffix = f" {format_clock(notice.display_time, clock_format)}"
        font = self.text_font
        if suffix and clock_format == "12h":
            # Keep AM/PM visible even when the longer clock leaves less room for the notice.
            text = truncate_to_fit(notice.text, font, available - font.getlength(suffix)) + suffix
        else:
            text = truncate_to_fit(notice.text + suffix, font, available)
        draw.text((x, middle), text, "black", font=font, anchor="lm")


class HdNoticeBoardPanel(NoticeBoardPanel):
    """The board as the HD screen shows it: taller rows in bigger text, as a table with its text column aligned."""

    row_height = 28
    source_font = resized(SOURCE_FONT, 15)
    text_font = resized(TEXT_FONT, 15)
    aligned = True

    @classmethod
    def of(cls, panel: NoticeBoardPanel) -> Self:
        """Return `panel`'s board, drawn the HD screen's way."""
        return cls(panel.notices, panel.style)
