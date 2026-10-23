"""Every stop on screen in one panel, two to a row, with a marker for the ones paged out."""

from copy import copy
from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.utils.scaled_draw import ScaledDraw, scaled_canvas

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel
    from countdown_core.utils.clock import ClockFormat

STOP_WIDTH = 262  # the widest a stop gets; a narrower panel (a portrait screen) narrows them
COLUMNS = 2
COLUMN_GAP = 19  # between two stops side by side, the divider in its middle

# The rotation marker under the stops: a square per page of stops, filled for the one on
# screen. Squares on whole pixels, since small circles come out ragged on the 1-bit panel.
ROTATION_STRIP = 16  # taken from the bottom of the panel's height
SQUARE = 7
SQUARE_GAP = 6


@dataclass(frozen=True)
class Rotation:
    """Which page of the configured stops is on screen, when they don't all fit on one."""

    page: int  # from 0
    pages: int


class CombinedArrivalPanel(Panel):
    """The stops on screen side by side, cropped to what they drew so the weather can sit right beside them."""

    def __init__(self, arrival_panels: list[AbstractArrivalPanel], rotation: Rotation | None = None) -> None:
        """`arrival_panels`: the stops on screen; `rotation`: where they are among all of them, if not all fit."""
        super().__init__()
        self.arrival_panels = arrival_panels
        self.rotation = rotation
        # How many times its own size it's drawn (see scaled()); 1 on the 7.5" screens.
        self.scale = 1

    def scaled(self, scale: int) -> CombinedArrivalPanel:
        """Return the same stops drawn `scale` times as big, for a screen with that many times the pixels."""
        bigger = copy(self)
        bigger.scale = scale
        return bigger

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        # Laid out in the stops' own pixels, drawn `scale` times as big.
        s = self.scale
        width, height = image_width // s, image_height // s
        img, draw = scaled_canvas(width, height, s)
        stops_height = height - (ROTATION_STRIP if self.rotation else 0)
        # The layout decides the height; the stops share it, a row at a time.
        rows = max(1, ceil(len(self.arrival_panels) / COLUMNS))
        stop_height = stops_height // rows
        stop_width = min(STOP_WIDTH, (width - COLUMN_GAP) // COLUMNS)
        x = 0
        y = 0
        for idx, panel in enumerate(self.arrival_panels):
            if idx % COLUMNS == 1:
                x += 9
                draw.line((x, 10, x, stops_height), fill="gray", width=1)
                x += 10
            rendered = panel.scaled(s).render(stop_width * s, stop_height * s, clock_format=clock_format)
            img.paste(rendered, (x * s, y * s), rendered)
            x += rendered.size[0] // s
            if idx % COLUMNS == COLUMNS - 1:
                y += stop_height
                x = 0
        if self.rotation:
            columns = min(COLUMNS, len(self.arrival_panels))
            self._draw_rotation(draw, self.rotation, columns * stop_width + (columns - 1) * COLUMN_GAP, stops_height)
        bus_panel_bbox = img.getbbox()
        if bus_panel_bbox is None:
            # Nothing drawn: crop(None) would mean "the whole image", not "nothing".
            return img.crop((0, 0, 0, image_height))
        return img.crop(bus_panel_bbox)

    @staticmethod
    def _draw_rotation(draw: ScaledDraw, rotation: Rotation, stops_width: int, top: int) -> None:
        """Draw the squares, centred under the stops in the strip below them."""
        row_width = rotation.pages * (SQUARE + SQUARE_GAP) - SQUARE_GAP
        x = (stops_width - row_width) // 2
        y = top + (ROTATION_STRIP - SQUARE) // 2 + 1
        for i in range(rotation.pages):
            box = (x, y, x + SQUARE - 1, y + SQUARE - 1)
            if i == rotation.page:
                draw.rectangle(box, fill="black")
            else:
                draw.rectangle(box, outline="black", width=1)
            x += SQUARE + SQUARE_GAP
