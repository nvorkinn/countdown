from dataclasses import dataclass
from math import ceil

from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_core.utils.clock import ClockFormat

STOP_WIDTH = 262  # the widest a stop gets; a narrower panel (a portrait screen) narrows them
COLUMNS = 2
COLUMN_GAP = 19  # between two stops side by side, the divider in its middle

# The rotation marker under the stops: a square per configured stop, filled for those on
# screen. Squares on whole pixels, since small circles come out ragged on the 1-bit panel.
ROTATION_STRIP = 16  # taken from the bottom of the panel's height
SQUARE = 7
SQUARE_GAP = 6


@dataclass(frozen=True)
class Rotation:
    """Where the stops on screen are among all the configured ones, when they don't all fit."""

    shown: frozenset[int]  # indexes into the configured stops
    total: int


class CombinedArrivalPanel(Panel):
    def __init__(self, arrival_panels: list[AbstractArrivalPanel], rotation: Rotation | None = None):
        super().__init__()
        self.arrival_panels = arrival_panels
        self.rotation = rotation

    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        stops_height = image_height - (ROTATION_STRIP if self.rotation else 0)
        # The layout decides the height; the stops share it, a row at a time.
        rows = max(1, ceil(len(self.arrival_panels) / COLUMNS))
        stop_height = stops_height // rows
        stop_width = min(STOP_WIDTH, (image_width - COLUMN_GAP) // COLUMNS)
        x = 0
        y = 0
        for idx, panel in enumerate(self.arrival_panels):
            if idx % COLUMNS == 1:
                x += 9
                draw.line((x, 10, x, stops_height), fill="gray", width=1)
                x += 10
            rendered = panel.render(stop_width, stop_height, clock_format=clock_format)
            img.paste(rendered, (x, y), rendered)
            x += rendered.size[0]
            if idx % COLUMNS == COLUMNS - 1:
                y += stop_height
                x = 0
        if self.rotation:
            columns = min(COLUMNS, len(self.arrival_panels))
            self._draw_rotation(draw, columns * stop_width + (columns - 1) * COLUMN_GAP, stops_height)
        bus_panel_bbox = img.getbbox()
        if bus_panel_bbox is None:
            # Nothing drawn: crop(None) would mean "the whole image", not "nothing".
            return img.crop((0, 0, 0, image_height))
        return img.crop(bus_panel_bbox)

    def _draw_rotation(self, draw: ImageDraw.ImageDraw, stops_width: int, top: int) -> None:
        """The squares, centred under the stops in the strip below them."""
        assert self.rotation is not None
        row_width = self.rotation.total * (SQUARE + SQUARE_GAP) - SQUARE_GAP
        x = (stops_width - row_width) // 2
        y = top + (ROTATION_STRIP - SQUARE) // 2 + 1
        for i in range(self.rotation.total):
            box = (x, y, x + SQUARE - 1, y + SQUARE - 1)
            if i in self.rotation.shown:
                draw.rectangle(box, fill="black")
            else:
                draw.rectangle(box, outline="black", width=1)
            x += SQUARE + SQUARE_GAP
