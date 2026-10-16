from datetime import datetime
from math import ceil, floor, log10

from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.i18n import N_, _, format_date
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI

TITLE_FONT = GOOGLE_SEMI.font_variant(size=16)
LABEL_FONT = GOOGLE_REGULAR.font_variant(size=12)
NO_DATA_FONT = GOOGLE_REGULAR.font_variant(size=16)

PADDING = 4  # blank border round the whole panel
TITLE_GAP = 8  # between the title and the tallest bar
LABEL_GAP = 4  # between the baseline and the x-axis labels
MIN_LABEL_SPACING = 8  # horizontal gap kept between neighbouring x-axis labels
BASELINE_WIDTH = 2
BAR_FILL = 0.8  # fraction of each slot the bar covers; the rest is the gap between bars
AXIS_WIDTH = 2
TICK_LENGTH = 3  # y-axis tick marks, sticking out left of the axis
TICK_LABEL_GAP = 3  # between a y-axis tick label and its tick mark
TARGET_Y_TICKS = 4  # roughly how many y-axis intervals to aim for
GRID_DOT_SPACING = 4  # gridlines are dotted - there's no grey on the e-paper

PAGES = [
    {
        "title": N_("Last 24 hours (kWh)"),
        "label_fn": lambda iso: datetime.fromisoformat(iso).strftime("%H:%M"),
    },
    {
        "title": N_("Last 31 days (kWh)"),
        "label_fn": lambda iso: format_date(datetime.fromisoformat(iso), "dd MMM"),
    },
    {
        "title": N_("This year (kWh)"),
        "label_fn": lambda iso: format_date(datetime.fromisoformat(iso), "MMM"),
    },
]


class EnergyPanel(Panel):
    def __init__(self, readings: list[dict], page_index: int):
        super().__init__()
        self.page_index = page_index
        self.readings = readings

    def render(self, image_width: int, image_height: int) -> Image.Image:
        """A bar chart of one page's readings ({"start": iso-string, "kwh": float} dicts).
        Bars on whole-pixel edges and 1-bit text, so there's nothing grey to dither."""
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        page = PAGES[self.page_index]

        title = page["title"]
        draw.text((PADDING, PADDING), _(title), "black", font=TITLE_FONT)

        if not self.readings:
            draw.text((image_width / 2, image_height / 2), "No data", "black", font=NO_DATA_FONT, anchor="mm")
            return img

        labels = [page["label_fn"](row["start"]) for row in self.readings]
        values = [row["kwh"] for row in self.readings]

        step, axis_max = self._y_scale(max(values))
        ticks = [i * step for i in range(round(axis_max / step) + 1)]
        tick_labels = [f"{tick:g}" for tick in ticks]

        label_height = LABEL_FONT.getbbox("0123456789:")[3]
        widest_tick_label = max(LABEL_FONT.getlength(label) for label in tick_labels)
        axis_x = PADDING + ceil(widest_tick_label) + TICK_LABEL_GAP + TICK_LENGTH
        chart_left = axis_x + AXIS_WIDTH
        chart_right = image_width - PADDING
        chart_top = PADDING + TITLE_FONT.getbbox(page["title"])[3] + TITLE_GAP
        baseline = image_height - PADDING - label_height - LABEL_GAP - BASELINE_WIDTH
        slot_width = (chart_right - chart_left) / len(values)

        def y_of(kwh: float) -> int:
            return baseline - round(max(kwh, 0) / axis_max * (baseline - chart_top))

        self._draw_y_axis(draw, ticks, tick_labels, y_of, axis_x, chart_right, chart_top, baseline)
        self._draw_bars(draw, values, chart_left, baseline, slot_width, y_of)
        draw.rectangle((axis_x, baseline, chart_right - 1, baseline + BASELINE_WIDTH - 1), fill="black")
        self._draw_labels(draw, labels, chart_left, baseline + BASELINE_WIDTH + LABEL_GAP, slot_width, image_width)
        return img

    @staticmethod
    def _y_scale(peak: float) -> tuple[float, float]:
        """A round tick step (1, 2, 2.5 or 5 times a power of ten) giving about
        TARGET_Y_TICKS intervals, and the axis top: the first tick at or above the
        tallest bar."""
        if peak <= 0:
            return 1.0, 1.0
        rough = peak / TARGET_Y_TICKS
        magnitude = 10 ** floor(log10(rough))
        step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= rough)
        return step, ceil(peak / step - 1e-9) * step

    @staticmethod
    def _draw_y_axis(draw, ticks, tick_labels, y_of, axis_x, chart_right, chart_top, baseline):
        draw.rectangle((axis_x, chart_top, axis_x + AXIS_WIDTH - 1, baseline - 1), fill="black")
        for tick, label in zip(ticks, tick_labels, strict=True):
            y = y_of(tick)
            draw.line((axis_x - TICK_LENGTH, y, axis_x - 1, y), fill="black")
            draw.text((axis_x - TICK_LENGTH - TICK_LABEL_GAP, y), label, "black", font=LABEL_FONT, anchor="rm")
            if tick > 0:
                for x in range(axis_x + AXIS_WIDTH + GRID_DOT_SPACING, chart_right, GRID_DOT_SPACING):
                    draw.point((x, y), fill="black")

    @staticmethod
    def _draw_bars(draw, values, chart_left, baseline, slot_width, y_of):
        # Negative/zero readings just draw nothing.
        gap = max(1, round(slot_width * (1 - BAR_FILL) / 2))
        for i, value in enumerate(values):
            # Round each slot's edges, not the bar width, so bars stay evenly spread
            # rather than the rounding error piling up at the right-hand end.
            x0 = chart_left + round(i * slot_width) + gap
            x1 = chart_left + round((i + 1) * slot_width) - gap - 1
            top = y_of(value)
            if top < baseline and x1 >= x0:
                draw.rectangle((x0, top, x1, baseline - 1), fill="black")

    @staticmethod
    def _draw_labels(draw, labels, chart_left, y, slot_width, image_width):
        # Label every nth bar, n being just enough that the widest label fits
        # between neighbours without overlapping.
        widest = max(LABEL_FONT.getlength(label) for label in labels)
        stride = max(1, ceil((widest + MIN_LABEL_SPACING) / slot_width))
        for i in range(0, len(labels), stride):
            centre = chart_left + (i + 0.5) * slot_width
            half = LABEL_FONT.getlength(labels[i]) / 2
            # Skip a label hanging off the panel edge rather than nudging it in,
            # which would crowd its neighbour and break the even spacing.
            if centre - half >= 0 and centre + half <= image_width:
                draw.text((centre, y), labels[i], "black", font=LABEL_FONT, anchor="ma")
