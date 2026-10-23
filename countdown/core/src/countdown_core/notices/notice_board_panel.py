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
SOURCE_GAP = 6  # between the source and the text
TAG_PADDING = 4  # either side of a severe or warning notice's source, inside its tag
TAG_MARGIN = 3  # above and below a tag, inside its row
TAG_RADIUS = 3
TAGGED = frozenset({Severity.SEVERE, Severity.WARNING})  # whose source is drawn in a tag: black if severe, outlined


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
        """Draw the style's chrome, then one notice per row: the source and as much text as fits.

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
            text_x = left + max(self._source_width(notice) for notice in shown) + SOURCE_GAP
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
        """Draw `notice`'s row with its top at `y`, between the x coordinates `span`; its text from `text_x`, if set.

        A severe notice's source is white in a black tag and a warning's is in an outlined one, so they stand out
        from the rest without a marker in front of every row.
        """
        left, right = span
        middle = y + self.row_height // 2
        source_width = self._source_width(notice)
        if notice.severity in TAGGED:
            tag = (left, y + TAG_MARGIN, left + source_width - 1, y + self.row_height - TAG_MARGIN - 1)
            if notice.severity == Severity.SEVERE:
                draw.rounded_rectangle(tag, TAG_RADIUS, fill="black")
            else:
                draw.rounded_rectangle(tag, TAG_RADIUS, outline="black", width=2)
            colour = "white" if notice.severity == Severity.SEVERE else "black"
            draw.text((left + TAG_PADDING, middle), notice.source, colour, font=self.source_font, anchor="lm")
        else:
            draw.text((left, middle), notice.source, "black", font=self.source_font, anchor="lm")
        x = text_x if text_x is not None else left + source_width + SOURCE_GAP
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

    def _source_width(self, notice: Notice) -> int:
        """Return how wide `notice`'s source is drawn, with its tag if it has one."""
        width = round(self.source_font.getlength(notice.source))
        return width + 2 * TAG_PADDING if notice.severity in TAGGED else width


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


class NoticeBannerPanel(Panel):
    """The most important notice on one black line, and how many more there are: the notice board in brief."""

    scales = True

    HEIGHT = 26

    def __init__(self, notices: list[Notice]) -> None:
        """`notices` already ranked, most important first (as the notice board has them)."""
        super().__init__()
        self.notices = notices

    @classmethod
    def of(cls, panel: NoticeBoardPanel) -> Self:
        """Return the banner for `panel`'s notices."""
        return cls(panel.notices)

    @override
    def fitted_height(self, max_height: int) -> int:
        """Return a line's height, or nothing with no notices."""
        return min(max_height, self.HEIGHT) if self.notices else 0

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, draw = scaled_canvas(image_width, image_height)
        if not self.notices:
            return img
        notice, more = self.notices[0], len(self.notices) - 1
        draw.rounded_rectangle((0, 0, image_width - 1, image_height - 1), 6, fill="black")
        middle = image_height / 2
        font, source_font = resized(TEXT_FONT, 14), resized(SOURCE_FONT, 14)
        x = 10
        draw.text((x, middle), notice.source, "white", font=source_font, anchor="lm")
        x += round(source_font.getlength(notice.source)) + 12
        right = image_width - 10
        if more:
            label = _("+{count} more").format(count=more)
            draw.text((right, middle), label, "white", font=resized(TEXT_FONT, 12), anchor="rm")
            right -= round(resized(TEXT_FONT, 12).getlength(label)) + 14
        suffix = f" {format_clock(notice.display_time, clock_format)}" if notice.display_time is not None else ""
        draw.text((x, middle), truncate_to_fit(notice.text + suffix, font, right - x), "white", font=font, anchor="lm")
        return img
