from math import ceil

from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_core.utils.clock import ClockFormat

STOP_WIDTH = 262  # the widest a stop gets; a narrower panel (a portrait screen) narrows them
COLUMNS = 2
COLUMN_GAP = 19  # between two stops side by side, the divider in its middle


class CombinedArrivalPanel(Panel):
    def __init__(self, arrival_panels: list[AbstractArrivalPanel]):
        super().__init__()
        self.arrival_panels = arrival_panels

    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        # The layout decides the height; the stops share it, a row at a time.
        rows = max(1, ceil(len(self.arrival_panels) / COLUMNS))
        stop_height = image_height // rows
        stop_width = min(STOP_WIDTH, (image_width - COLUMN_GAP) // COLUMNS)
        x = 0
        y = 0
        for idx, panel in enumerate(self.arrival_panels):
            if idx % COLUMNS == 1:
                x += 9
                draw.line((x, 10, x, img.size[1]), fill="gray", width=1)
                x += 10
            rendered = panel.render(stop_width, stop_height, clock_format=clock_format)
            img.paste(rendered, (x, y), rendered)
            x += rendered.size[0]
            if idx % COLUMNS == COLUMNS - 1:
                y += stop_height
                x = 0
        bus_panel_bbox = img.getbbox()
        if bus_panel_bbox is None:
            # Nothing drawn: crop(None) would mean "the whole image", not "nothing".
            return img.crop((0, 0, 0, image_height))
        return img.crop(bus_panel_bbox)
