"""The newspaper's foot: the electricity used, as a bar chart under a double rule and a headline.

The headline names the energy panel's page ("Electricity, the last 24 hours"), with the unit and the source against
the right edge; the chart's axes are labelled in the newspaper's type, its gridlines dotted.
"""

from datetime import datetime
from math import ceil
from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.glow.energy_panel import PAGES, EnergyPanel
from countdown_core.i18n import N_, _
from countdown_core.newspaper.typesetting import double_rule, serif, serif_bold
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.utils.clock import format_clock
from countdown_core.utils.scaled_draw import scaled_canvas

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw

# Each of the energy panel's pages (glow.energy_panel.PAGES): its headline, and the unit its bars are in.
TITLES = (
    (N_("Electricity, the last 24 hours"), N_("kWh per hour")),
    (N_("Electricity, the last 31 days"), N_("kWh per day")),
    (N_("Electricity, this year"), N_("kWh per month")),
)
SOURCE = "Glowmarkt"
# The headline's baseline and size, the note's size; where the chart starts under them.
TITLE_BASELINE = 30
TITLE_SIZE = 18
NOTE_SIZE = 12
CHART_TOP = 44
# The axes' labels: their size, and the room between them and the axes; how far apart the gridlines' dots are.
LABEL_SIZE = 12
LABEL_GAP = 5
GRID_STEP = 6
# How much of its slot a bar covers.
BAR_FILL = 0.78


class NewspaperEnergyPanel(Panel):
    """The energy panel's page as the newspaper's foot: a double rule, a headline, and the bar chart."""

    scales = True

    def __init__(self, energy: Panel) -> None:
        """`energy`: the Glowmarkt client's panel (an EnergyPanel), or a message in its place."""
        super().__init__()
        self.energy = energy

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        double_rule(d, 0, (0, image_width))
        energy = self.energy
        if not isinstance(energy, EnergyPanel):
            d.text((0, TITLE_BASELINE), _("Electricity"), "black", serif_bold(TITLE_SIZE), anchor="ls")
            if isinstance(energy, MessagePanel):
                d.text((0, CHART_TOP + 20), _(energy.message), "black", serif(14), anchor="ls")
            return img
        title, unit = TITLES[energy.page_index]
        d.text((0, TITLE_BASELINE), _(title), "black", serif_bold(TITLE_SIZE), anchor="ls")
        note = f"{_(unit)} \N{MIDDLE DOT} {SOURCE}"
        d.text((image_width, TITLE_BASELINE), note, "black", serif(NOTE_SIZE), anchor="rs")
        if not energy.readings:
            d.text((0, CHART_TOP + 20), _("No data"), "black", serif(14), anchor="ls")
            return img
        _draw_chart(d, energy, (image_width, image_height), clock_format)
        return img


def _draw_chart(d: ScaledDraw, energy: EnergyPanel, size: tuple[int, int], clock_format: ClockFormat) -> None:
    """Draw the readings as bars over a labelled baseline, with a labelled axis and dotted gridlines."""
    width, height = size
    font = serif(LABEL_SIZE)
    values = [row["kwh"] for row in energy.readings]
    step, top = EnergyPanel._y_scale(max(values))  # noqa: SLF001 -- the energy panel's own scale, so the charts agree
    ticks = [i * step for i in range(round(top / step) + 1)]
    axis_x = ceil(max(font.getlength(f"{tick:g}") for tick in ticks)) + LABEL_GAP + 4
    baseline = height - LABEL_SIZE - LABEL_GAP - 2
    chart_top = CHART_TOP + LABEL_SIZE // 2

    def y_of(kwh: float) -> int:
        return baseline - round(max(kwh, 0) / top * (baseline - chart_top))

    d.rectangle((axis_x, chart_top, axis_x + 1, baseline), fill="black")
    d.rectangle((axis_x, baseline, width - 1, baseline + 1), fill="black")
    for tick in ticks:
        y = y_of(tick)
        d.rectangle((axis_x - 4, y, axis_x - 1, y), fill="black")
        d.text((axis_x - LABEL_GAP - 4, y), f"{tick:g}", "black", font, anchor="rm")
        if tick:
            for x in range(axis_x + GRID_STEP, width, GRID_STEP):
                d.point((x, y), fill="black")

    left = axis_x + 4
    slot = (width - left) / len(values)
    gap = slot * (1 - BAR_FILL) / 2
    for i, value in enumerate(values):
        x0, x1 = round(left + i * slot + gap), round(left + (i + 1) * slot - gap) - 1
        if y_of(value) < baseline and x1 >= x0:
            d.rectangle((x0, y_of(value), x1, baseline - 1), fill="black")

    labels = _labels(energy, clock_format)
    stride = max(1, ceil((max(font.getlength(label) for label in labels) + 4 * LABEL_GAP) / slot))
    for i in range(0, len(labels), stride):
        centre = left + (i + 0.5) * slot
        if centre + font.getlength(labels[i]) / 2 <= width:
            d.text((centre, baseline + 2 + LABEL_GAP), labels[i], "black", font, anchor="ma")


def _labels(energy: EnergyPanel, clock_format: ClockFormat) -> list[str]:
    """Return each reading's label, as the energy panel's page labels them: a clock time, a day, or a month."""
    label = PAGES[energy.page_index]["label_fn"]
    return [
        label(row["start"]) if label is not None else format_clock(datetime.fromisoformat(row["start"]), clock_format)
        for row in energy.readings
    ]
