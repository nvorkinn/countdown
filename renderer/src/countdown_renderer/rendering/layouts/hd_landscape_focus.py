"""The dashboard on a 1872x1404 HD screen hung landscape, the departures first: the focus layout.

On the left, each stop's next departure huge, and under both the rest of each stop in a list, side by side
(tfl.focus_panel). On the right, past a rule: the time and the date, the weather now big with the coming hours under it
(weather.focus_weather_panel), then the energy chart, and Spotify with the developer strip under it. The notices run
along the foot, under a rule, only as tall as their rows.

Its four variants say whether Spotify and the energy chart show (see layouts.hd.variants); what one doesn't show
leaves its room to the weather, which then lists the forecast's later pages too. The stops are drawn about two and a
quarter times as big, the rest twice (see layouts.hd).
"""

from typing import TYPE_CHECKING, override

from PIL import ImageDraw

from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.notices.focus_notices_panel import FocusNoticesPanel
from countdown_renderer.notices.notice_board_panel import NoticeBoardPanel
from countdown_renderer.rendering.layout import NOTICES_MESSAGE_HEIGHT, Frame
from countdown_renderer.rendering.layouts.hd import SCALE, coming_hours, stops_of, variants, weather_parts
from countdown_renderer.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_renderer.rendering.layouts.hd_portrait_designs import HdDesignLayout
from countdown_renderer.system_screens.message_panel import MessagePanel
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_renderer.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_renderer.tfl.focus_panel import FocusPanel
from countdown_renderer.weather.focus_weather_panel import FocusWeatherPanel

if TYPE_CHECKING:
    from PIL import Image

    from countdown_renderer.panel import Panel
    from countdown_renderer.rendering.screen import Canvas
    from countdown_renderer.utils.clock import ClockFormat

# In screen pixels: the room either side of what's drawn, above it, and below the notices; between parts of a column.
MARGIN = 48
TOP = 44
EDGE = 30
SECTION_GAP = 30
# The stops' column's width, the room either side of the rule beside it, and the rules' widths: down the middle,
# over the notices, and between the right column's parts.
LEFT_WIDTH = 1060
RULE_ROOM = 36
RULE = 3
NOTICES_RULE = 4
PART_RULE = 2
# How many screen pixels each of the stops' takes, either way: about as big as the sidebar layout draws them.
FOCUS_SCALE = 2.25
# In their own pixels (at SCALE): the energy chart's least height, Spotify's area's, and the most the notices may take.
ENERGY_HEIGHT = 130
SPOTIFY_HEIGHT = 80
NOTICES_ROOM = 120

BLACK = (0, 0, 0, 255)


class HdLandscapeFocusLayout(HdDesignLayout):
    """Each stop's next departure huge and the rest in lists on the left; the time, the weather and the rest right."""

    layout_id = "focus"
    canvases = frozenset({HD_LANDSCAPE})

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        panels = self._hd_panels(panels, wide_spotify=False)
        draw = ImageDraw.Draw(img)
        bottom = canvas.height - EDGE
        notices_top = self._draw_foot(img, panels, (MARGIN, canvas.width - 2 * MARGIN), bottom, clock_format)
        if notices_top != bottom:
            rule = notices_top - SECTION_GAP // 2
            draw.line((MARGIN, rule, canvas.width - MARGIN, rule), fill=BLACK, width=NOTICES_RULE)
            bottom = rule - SECTION_GAP // 2

        self._draw_stops(img, panels[ClientClasses.TFL.api_name], (MARGIN, TOP, LEFT_WIDTH, bottom - TOP), clock_format)
        rule = MARGIN + LEFT_WIDTH + RULE_ROOM
        draw.line((rule, TOP, rule, bottom), fill=BLACK, width=RULE)

        x = rule + RULE_ROOM
        self._draw_right(img, panels, (x, canvas.width - MARGIN - x), bottom, clock_format)
        return Frame(img)

    def _draw_foot(
        self,
        img: Image.Image,
        panels: dict[str, Panel],
        column: tuple[int, int],
        bottom: int,
        clock_format: ClockFormat,
    ) -> int:
        """Draw the notices along the foot, ending at `bottom`; return their top (`bottom` with none to show).

        A message in their place is drawn as the other layouts draw it.
        """
        notices = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if isinstance(notices, NoticeBoardPanel):
            notices = FocusNoticesPanel(notices.notices)
        if notices is None:
            return bottom
        most = NOTICES_MESSAGE_HEIGHT if isinstance(notices, MessagePanel) else NOTICES_ROOM
        height = notices.fitted_height(most)
        if not height:
            return bottom
        x, width = column
        top = bottom - SCALE * height
        self._place_scaled(img, notices, (x, top), (width // SCALE, height), clock_format)
        return top

    def _draw_stops(
        self, img: Image.Image, arrivals: Panel, box: tuple[int, int, int, int], clock_format: ClockFormat
    ) -> None:
        """Draw the stops in `box` (left, top, width, height): the focus panel, or what's there instead (a message)."""
        x, y, width, height = box
        if isinstance(arrivals, (CombinedArrivalPanel, AbstractArrivalPanel)):
            rotation = arrivals.rotation if isinstance(arrivals, CombinedArrivalPanel) else None
            panel = FocusPanel(stops_of(arrivals), rotation)
            size = (int(width / FOCUS_SCALE), int(height / FOCUS_SCALE))
            self._place_scaled(img, panel, (x, y), size, clock_format, FOCUS_SCALE)
        else:
            self._place_scaled(img, arrivals, (x, y), (width // SCALE, height // SCALE), clock_format)

    def _draw_right(
        self,
        img: Image.Image,
        panels: dict[str, Panel],
        column: tuple[int, int],
        bottom: int,
        clock_format: ClockFormat,
    ) -> None:
        """Draw the right column down to `bottom`: the weather, the energy chart, Spotify and the developer strip.

        Spotify's area is as tall as it needs; the weather takes the rest, but for the chart's least height, with as
        many of the forecast's later pages as fit, and the chart gets what it leaves.
        """
        x, width = column
        draw = ImageDraw.Draw(img)
        # Spotify's song first, the developer strip (if any) under it.
        area = reversed(self._spotify_area(panels, SPOTIFY_HEIGHT)) if self.SHOWS_SPOTIFY else ()
        area = [(panel, height) for panel, height in area if height]
        if area:
            top = bottom - SCALE * sum(height for _panel, height in area) - SECTION_GAP // 2 * (len(area) - 1)
            y = top
            for panel, height in area:
                self._place_scaled(img, panel, (x, y), (width // SCALE, height), clock_format)
                y += SCALE * height + SECTION_GAP // 2
            bottom = self._rule_above(draw, (x, width), top)
        weather = panels[ClientClasses.WEATHER.api_name]
        panel = FocusWeatherPanel(*weather_parts(weather), coming_hours(weather))
        room = bottom - TOP - (SCALE * ENERGY_HEIGHT + SECTION_GAP if self.SHOWS_ENERGY else 0)
        height = panel.fitted_height(room // SCALE)
        self._place_scaled(img, panel, (x, TOP), (width // SCALE, height), clock_format)
        if self.SHOWS_ENERGY:
            # The chart gets the room the weather leaves.
            top = TOP + SCALE * height + SECTION_GAP
            energy = panels[ClientClasses.GLOWMARKT.api_name]
            self._place_scaled(img, energy, (x, top), (width // SCALE, (bottom - top) // SCALE), clock_format)
            self._rule_above(draw, (x, width), top)

    @staticmethod
    def _rule_above(draw: ImageDraw.ImageDraw, column: tuple[int, int], top: int) -> int:
        """Draw a rule across `column` (left, width) a gap above `top`; return where what's above it ends."""
        x, width = column
        rule = top - SECTION_GAP // 2
        draw.line((x, rule, x + width, rule), fill=BLACK, width=PART_RULE)
        return top - SECTION_GAP


HD_LANDSCAPE_FOCUS_LAYOUTS = variants(HdLandscapeFocusLayout)
