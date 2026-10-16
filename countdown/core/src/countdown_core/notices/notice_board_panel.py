"""How the notice board is drawn."""

from typing import override

from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_styles import STYLES, NoticeStyle
from countdown_core.utils.clock import ClockFormat, format_clock
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, truncate_to_fit

SOURCE_FONT = GOOGLE_SEMI
TEXT_FONT = GOOGLE_REGULAR

ROW_HEIGHT = 22
MARKER_SIZE = 14  # the square severity marker at the start of each row
MARKER_GAP = 6  # between the marker and the source tag
SOURCE_GAP = 6  # between the source tag and the text


class NoticeBoardPanel(Panel):
    """The notice board: its style's title and frame, then a row per notice, the most severe first."""

    def __init__(self, notices: list[Notice], style: NoticeStyle = "hero") -> None:
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
        return min(max_height, style.top + len(self.notices) * ROW_HEIGHT + style.bottom)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        """Draw the style's chrome, then one notice per row: a severity marker, the source and as much text as fits.

        If there are more current notices than rows, the last row says how many didn't fit.
        """
        style = STYLES[self.style]
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        style.draw(draw, image_width, image_height, len(self.notices))

        left, right = style.inset, image_width - style.inset
        rows = max(1, (image_height - style.top - style.bottom) // ROW_HEIGHT)
        current = [n for n in self.notices if n.severity != Severity.PLANNED]
        planned = [n for n in self.notices if n.severity == Severity.PLANNED]
        if len(current) > rows:
            shown, more = current[: rows - 1], len(current) - (rows - 1)
        else:
            # Planned works only fill the rows left over, and don't count as "more".
            shown, more = current + planned[: rows - len(current)], 0
        for i, notice in enumerate(shown):
            self._draw_row(draw, notice, style.top + i * ROW_HEIGHT, (left, right), clock_format)
        if more:
            y = style.top + len(shown) * ROW_HEIGHT + ROW_HEIGHT // 2
            draw.text((left, y), _("+{count} more").format(count=more), "black", font=TEXT_FONT, anchor="lm")
        return img

    @staticmethod
    def _draw_row(
        draw: ImageDraw.ImageDraw, notice: Notice, y: int, span: tuple[int, int], clock_format: ClockFormat
    ) -> None:
        """Draw `notice`'s row with its top at `y`, between the x coordinates `span`."""
        left, right = span
        middle = y + ROW_HEIGHT // 2
        box = (left, middle - MARKER_SIZE // 2, left + MARKER_SIZE - 1, middle + MARKER_SIZE // 2 - 1)
        centre = ((box[0] + box[2] + 1) / 2, middle)
        if notice.severity == Severity.SEVERE:
            draw.rectangle(box, fill="black")
            draw.text(centre, "!", "white", font=SOURCE_FONT, anchor="mm")
        elif notice.severity == Severity.WARNING:
            draw.rectangle(box, outline="black", width=2)
            draw.text(centre, "!", "black", font=SOURCE_FONT, anchor="mm")
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
        draw.text((x, middle), notice.source, "black", font=SOURCE_FONT, anchor="lm")
        x += round(SOURCE_FONT.getlength(notice.source)) + SOURCE_GAP
        available = right - x
        suffix = ""
        if notice.display_time is not None:
            suffix = f" {format_clock(notice.display_time, clock_format)}"
        if suffix and clock_format == "12h":
            # Keep AM/PM visible even when the longer clock leaves less room for the notice.
            text = truncate_to_fit(notice.text, TEXT_FONT, available - TEXT_FONT.getlength(suffix)) + suffix
        else:
            text = truncate_to_fit(notice.text + suffix, TEXT_FONT, available)
        draw.text((x, middle), text, "black", font=TEXT_FONT, anchor="lm")
