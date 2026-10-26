"""The notice board in two columns, as the HD landscape column layout draws it along the screen's foot.

The board's chrome (its style's title and count) runs across the whole width; the rows under it fill the left column
and the right in turn (the most important top left, then top right), each with its text aligned in its own column.
"""

from math import ceil
from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Severity
from countdown_renderer.notices.notice_board_panel import SOURCE_GAP, HdNoticeBoardPanel
from countdown_renderer.notices.notice_styles import STYLES
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_shared.i18n import _

if TYPE_CHECKING:
    from PIL import Image

    from countdown_renderer.notices.notice import Notice
    from countdown_renderer.utils.clock import ClockFormat

COLUMNS = 2
# Between the two columns, in the panel's own pixels.
COLUMN_GAP = 30


class TwoColumnNoticeBoardPanel(HdNoticeBoardPanel):
    """The HD notice board with its rows in two columns: half as tall for the same notices."""

    @override
    def fitted_height(self, max_height: int) -> int:
        """Leave room for a row per two notices, or nothing at all when there are none."""
        if not self.notices:
            return 0
        style = STYLES[self.style]
        return min(max_height, style.top + ceil(len(self.notices) / COLUMNS) * self.row_height + style.bottom)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        """Draw the style's chrome across the width, then the notices left and right in turn.

        If there are more current notices than places, the last place says how many didn't fit.
        """
        style = STYLES[self.style]
        img, draw = scaled_canvas(image_width, image_height, (255, 255, 255, 255))
        style.draw(draw, image_width, image_height, len(self.notices))

        left, right = style.inset, image_width - style.inset
        column = (right - left - COLUMN_GAP * (COLUMNS - 1)) // COLUMNS
        places = COLUMNS * max(1, (image_height - style.top - style.bottom) // self.row_height)
        shown, more = _shown(self.notices, places)
        spans = [(left + i * (column + COLUMN_GAP), left + i * (column + COLUMN_GAP) + column) for i in range(COLUMNS)]
        text_xs = [self._text_x(shown[i::COLUMNS], spans[i][0]) for i in range(COLUMNS)]
        for i, notice in enumerate(shown):
            y = style.top + (i // COLUMNS) * self.row_height
            column_left, column_right = spans[i % COLUMNS]
            self._draw_row(draw, notice, y, (column_left, text_xs[i % COLUMNS], column_right), clock_format)
        if more:
            i = len(shown)
            y = style.top + (i // COLUMNS) * self.row_height + self.row_height // 2
            x = spans[i % COLUMNS][0]
            draw.text((x, y), _("+{count} more").format(count=more), "black", font=self.text_font, anchor="lm")
        return img

    def _text_x(self, notices: list[Notice], left: int) -> int:
        """Where the text of a column's `notices` starts: past the widest source among them."""
        widest = max((round(self.source_font.getlength(notice.source)) for notice in notices), default=0)
        return left + widest + SOURCE_GAP


def _shown(notices: list[Notice], places: int) -> tuple[list[Notice], int]:
    """Return the notices there are `places` for, and how many current ones didn't fit (counted in the last place).

    As NoticeBoardPanel chooses them: planned works only fill the places left over, and don't count as more.
    """
    current = [notice for notice in notices if notice.severity != Severity.PLANNED]
    planned = [notice for notice in notices if notice.severity == Severity.PLANNED]
    if len(current) > places:
        return current[: places - 1], len(current) - (places - 1)
    return current + planned[: places - len(current)], 0
