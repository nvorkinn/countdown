"""How the notice board is drawn."""

from typing import TYPE_CHECKING, ClassVar, Self, override

from countdown_core.i18n import _
from countdown_renderer.notices.notice import Notice, Severity
from countdown_renderer.notices.notice_styles import STYLES
from countdown_renderer.panel import Panel
from countdown_renderer.utils.clock import ClockFormat, format_clock
from countdown_renderer.utils.scaled_draw import ScaledDraw, scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, resized, truncate_to_fit

if TYPE_CHECKING:
    from PIL import Image, ImageFont

    from countdown_renderer.style import Style

SOURCE_FONT = GOOGLE_SEMI
TEXT_FONT = GOOGLE_REGULAR

ROW_HEIGHT = 22
SOURCE_GAP = 6  # between the source and the text
ICON_SIZE = 14  # the square "!" icon a severe (black) or warning (outlined) notice has at the start of its text
ICON_GAP = 5  # between the icon and the text
ICONED = frozenset({Severity.SEVERE, Severity.WARNING})  # the rest have no icon


class NoticeBoardPanel(Panel):
    """The notice board: its style's title and frame, then a row per notice, the most severe first."""

    scales = True

    # The rows: how tall, and in which fonts.
    row_height: ClassVar[int] = ROW_HEIGHT
    source_font: ClassVar[ImageFont.FreeTypeFont] = SOURCE_FONT
    text_font: ClassVar[ImageFont.FreeTypeFont] = TEXT_FONT

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
        """Draw the style's chrome, then a row per notice: its source, then its text (after any severity icon).

        The rows are a table: every source starts at the edge, and every text where the widest source shown ends. If
        there are more current notices than rows, the last row says how many didn't fit.
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
        widest = max((round(self.source_font.getlength(notice.source)) for notice in shown), default=0)
        columns = (left, left + widest + SOURCE_GAP, right)
        for i, notice in enumerate(shown):
            self._draw_row(draw, notice, style.top + i * self.row_height, columns, clock_format)
        if more:
            y = style.top + len(shown) * self.row_height + self.row_height // 2
            draw.text((left, y), _("+{count} more").format(count=more), "black", font=self.text_font, anchor="lm")
        return img

    def _draw_row(
        self, draw: ScaledDraw, notice: Notice, y: int, columns: tuple[int, int, int], clock_format: ClockFormat
    ) -> None:
        """Draw `notice`'s row with its top at `y`: its source from the first x in `columns`, its text from the second.

        The text is cut to end by the third. A severe or warning notice's text starts with its icon.
        """
        source_x, x, right = columns
        middle = y + self.row_height // 2
        draw.text((source_x, middle), notice.source, "black", font=self.source_font, anchor="lm")
        if notice.severity in ICONED:
            box = (x, middle - ICON_SIZE // 2, x + ICON_SIZE - 1, middle + ICON_SIZE // 2 - 1)
            centre = (x + ICON_SIZE / 2, middle)
            if notice.severity == Severity.SEVERE:
                draw.rectangle(box, fill="black")
                draw.text(centre, "!", "white", font=self.source_font, anchor="mm")
            else:
                draw.rectangle(box, outline="black", width=2)
                draw.text(centre, "!", "black", font=self.source_font, anchor="mm")
            x += ICON_SIZE + ICON_GAP
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
    """The board as the HD screen shows it: taller rows in bigger text."""

    row_height = 28
    source_font = resized(SOURCE_FONT, 15)
    text_font = resized(TEXT_FONT, 15)

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
        font, source_font, icon_font = resized(TEXT_FONT, 14), resized(SOURCE_FONT, 14), resized(SOURCE_FONT, 15)
        x = 10
        draw.text((x, middle), notice.source, "white", font=source_font, anchor="lm")
        x += round(source_font.getlength(notice.source)) + 12
        if notice.severity in ICONED:  # the icon starts the text, as on the board
            draw.text((x + 3, middle), "!", "white", font=icon_font, anchor="mm")
            x += 14
        right = image_width - 10
        if more:
            label = _("+{count} more").format(count=more)
            draw.text((right, middle), label, "white", font=resized(TEXT_FONT, 12), anchor="rm")
            right -= round(resized(TEXT_FONT, 12).getlength(label)) + 14
        suffix = f" {format_clock(notice.display_time, clock_format)}" if notice.display_time is not None else ""
        draw.text((x, middle), truncate_to_fit(notice.text + suffix, font, right - x), "white", font=font, anchor="lm")
        return img
