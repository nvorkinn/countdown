"""The notices as the HD landscape screen's focus layout draws them: along its foot, one after another on a line or two.

Each notice is its source in bold and its text, the most important first, as many to a line as fit; one that doesn't
fit on a line starts the next. What's left over when the lines are full is cut short on the last, and counted at its
end ("+2 more"), planned works aside (as the notice board leaves them out of its count). Laid out in pixels sized for
the 7.5" screens; the layout draws it twice as big (rendering.layouts.hd_landscape_focus).
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.notices.notice import Severity
from countdown_core.notices.notice_styles import CAMPAIGN
from countdown_core.utils.clock import format_clock
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, google_symbols, resized, truncate_to_fit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.notices.notice import Notice
    from countdown_core.utils.clock import ClockFormat

# The most lines, and each one's height.
LINES = 2
LINE = 26
# The room the megaphone before the first line takes; between a notice's source and its text, and two notices.
ICON_ROOM = 28
SOURCE_GAP = 6
NOTICE_GAP = 32
# The least room worth starting a notice in, cut short, at the end of the last line.
LEAST = 80

SOURCE_FONT = resized(GOOGLE_SEMI, 15)
TEXT_FONT = resized(GOOGLE_REGULAR, 15)

# A notice placed on a line: its line, where it starts, and its text (cut short to fit, if it had to be).
Placed = tuple[int, float, "Notice", str]


class FocusNoticesPanel(Panel):
    """The notices one after another along a line or two, the most important first."""

    scales = True

    def __init__(self, notices: Sequence[Notice]) -> None:
        """`notices` already ranked, most important first (as the notice board has them)."""
        super().__init__()
        self.notices = tuple(notices)

    @override
    def fitted_height(self, max_height: int) -> int:
        """Return a line's height per line there's room for, up to LINES; nothing with no notices."""
        if not self.notices:
            return 0
        return min(max_height, LINES * LINE)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        lines = max(1, min(LINES, image_height // LINE))
        if not self.notices:
            return img
        placed, more = _flow(self.notices, image_width - ICON_ROOM, lines, clock_format)
        if more:
            label = _("+{count} more").format(count=more)
            room = image_width - ICON_ROOM - TEXT_FONT.getlength(label) - NOTICE_GAP / 2
            placed, more = _flow(self.notices, room, lines, clock_format)
            d.text((image_width, (lines - 0.5) * LINE), label, "black", TEXT_FONT, anchor="rm")
        d.text((0, LINE / 2), CAMPAIGN, "black", resized(google_symbols(), 22), anchor="lm")
        for line, x, notice, text in placed:
            middle = (line + 0.5) * LINE
            d.text((ICON_ROOM + x, middle), notice.source, "black", SOURCE_FONT, anchor="lm")
            text_x = ICON_ROOM + x + SOURCE_FONT.getlength(notice.source) + SOURCE_GAP
            d.text((text_x, middle), text, "black", TEXT_FONT, anchor="lm")
        return img


def _flow(notices: Sequence[Notice], width: float, lines: int, clock_format: ClockFormat) -> tuple[list[Placed], int]:
    """Place `notices` along `lines` lines `width` long; return where each went, and how many didn't (planned aside)."""
    placed: list[Placed] = []
    line, x = 0, 0.0
    for i, notice in enumerate(notices):
        text = _text(notice, clock_format)
        source = SOURCE_FONT.getlength(notice.source) + SOURCE_GAP
        needed = source + TEXT_FONT.getlength(text)
        if x and x + needed > width and line + 1 < lines:
            line, x = line + 1, 0.0
        room = width - x - source
        if needed > width - x:
            if room < LEAST:
                return placed, _counted(notices[i:])
            text = truncate_to_fit(text, TEXT_FONT, room)
        placed.append((line, x, notice, text))
        x += source + TEXT_FONT.getlength(text) + NOTICE_GAP
        if x >= width and line + 1 >= lines:
            return placed, _counted(notices[i + 1 :])
    return placed, 0


def _counted(notices: Sequence[Notice]) -> int:
    """How many of `notices` the "+N more" counts: all but planned works."""
    return sum(notice.severity != Severity.PLANNED for notice in notices)


def _text(notice: Notice, clock_format: ClockFormat) -> str:
    """Return the notice's text, with the time it gives after it, if it gives one."""
    if notice.display_time is None:
        return notice.text
    return f"{notice.text} {format_clock(notice.display_time, clock_format)}"
