from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.notices.notice import Notice, Severity
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, truncate_to_fit

TITLE_FONT = GOOGLE_SEMI.font_variant(size=16)
SOURCE_FONT = GOOGLE_SEMI
TEXT_FONT = GOOGLE_REGULAR

PADDING = 4  # blank border round the whole panel
TITLE_GAP = 6  # between the title and the first notice
ROW_HEIGHT = 22
MARKER_SIZE = 14  # the square severity marker at the start of each row
MARKER_GAP = 6  # between the marker and the source tag
SOURCE_GAP = 6  # between the source tag and the text


class NoticeBoardPanel(Panel):
    def __init__(self, notices: list[Notice]):
        """`notices` already ranked, most important first."""
        self.notices = notices

    @staticmethod
    def _top() -> int:
        """Where the first row starts, below the title."""
        return PADDING + TITLE_FONT.getbbox("Notices")[3] + TITLE_GAP

    def fitted_height(self, max_height: int) -> int:
        """A row per notice, or nothing at all when there are none. At `max_height` the
        rows that don't fit are counted on the last one."""
        if not self.notices:
            return 0
        return min(max_height, self._top() + len(self.notices) * ROW_HEIGHT + PADDING)

    def render(self, image_width: int, image_height: int) -> Image.Image:
        """One notice per row: a severity marker, the source and as much of the text as
        fits. If there are more current notices than rows, the last row says how many
        didn't fit."""
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"

        draw.text((PADDING, PADDING), "Notices", "black", font=TITLE_FONT)
        top = self._top()
        rows = max(1, (image_height - PADDING - top) // ROW_HEIGHT)

        current = [n for n in self.notices if n.severity != Severity.PLANNED]
        planned = [n for n in self.notices if n.severity == Severity.PLANNED]
        if len(current) > rows:
            shown, more = current[: rows - 1], len(current) - (rows - 1)
        else:
            # Planned works only fill the rows left over, and don't count as "more".
            shown, more = current + planned[: rows - len(current)], 0
        for i, notice in enumerate(shown):
            self._draw_row(draw, notice, top + i * ROW_HEIGHT, image_width)
        if more:
            y = top + len(shown) * ROW_HEIGHT + ROW_HEIGHT // 2
            draw.text((PADDING, y), f"+{more} more", "black", font=TEXT_FONT, anchor="lm")
        return img

    @staticmethod
    def _draw_row(draw: ImageDraw.ImageDraw, notice: Notice, y: int, image_width: int) -> None:
        middle = y + ROW_HEIGHT // 2
        box = (PADDING, middle - MARKER_SIZE // 2, PADDING + MARKER_SIZE - 1, middle + MARKER_SIZE // 2 - 1)
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

        x = PADDING + MARKER_SIZE + MARKER_GAP
        draw.text((x, middle), notice.source, "black", font=SOURCE_FONT, anchor="lm")
        x += round(SOURCE_FONT.getlength(notice.source)) + SOURCE_GAP
        text = truncate_to_fit(notice.text, TEXT_FONT, image_width - PADDING - x)
        draw.text((x, middle), text, "black", font=TEXT_FONT, anchor="lm")
