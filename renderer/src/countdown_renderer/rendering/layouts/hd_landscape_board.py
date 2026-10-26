"""The HD screen hung landscape as a departure board: the two stops side by side, big, under a slim line with the time.

Top to bottom: the time, the date and the weather now in brief on one line (weather.board_weather_panels); the most
important notice on a black line, with how many more there are (notices.notice_board_panel.NoticeBannerPanel), if
there are any; the stops side by side, each a black bar with its name over a row per route (tfl.board_stop_panel),
two and a quarter times as big, as the sidebar layout draws its stops; and a strip along the foot with the weather now
on the left and, beside it, the energy chart and Spotify side by side.

What a variant doesn't show leaves its room in the strip to the other: without Glowmarkt, Spotify's now page is wide;
without Spotify, the energy chart has the room. With neither, the coming hours have it. The developer panel is a strip
above Spotify, as in the other layouts. The rest are drawn twice as big (see layouts.hd).
"""

from typing import TYPE_CHECKING, override

from PIL import Image, ImageDraw

from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.notices.notice_board_panel import NoticeBannerPanel, NoticeBoardPanel
from countdown_renderer.rendering.layout import Frame
from countdown_renderer.rendering.layouts.hd import (
    SCALE,
    HdVariantLayout,
    coming_hours,
    stops_of,
    variants,
    weather_parts,
)
from countdown_renderer.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_renderer.tfl.board_stop_panel import BoardStopPanel
from countdown_renderer.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_renderer.utils.utils import GAP
from countdown_renderer.weather.board_weather_panels import HEADER_HEIGHT, BoardHeaderPanel, BoardWeatherPanel
from countdown_renderer.weather.hd_sidebar_panel import HdHoursPanel

if TYPE_CHECKING:
    from countdown_renderer.panel import Panel
    from countdown_renderer.rendering.screen import Canvas
    from countdown_renderer.utils.clock import ClockFormat

# In screen pixels: the room either side of what's drawn, and under it; between the header line and what's under it,
# between the board and the strip, and between the two stops; the board's scale (as the sidebar layout's stops').
MARGIN = 40
EDGE = 30
HEADER_GAP = 8
BANNER_GAP = 24
STRIP_GAP = 40
COLUMN_GAP = 48
BOARD_SCALE = 2.25
# The strip along the foot, in its own pixels (at SCALE): its height, and the weather's width at its start; the room
# between the weather and what's beside it (with a rule in the middle), in screen pixels.
STRIP_HEIGHT = 160
WEATHER_WIDTH = 230
WEATHER_GAP = 48
# The energy chart's share of the room beside the weather, with Spotify beside it: a little less than in the other
# layouts, since the strip is taller and Spotify's cover as tall as it.
ENERGY_SHARE = 0.4
# The coming hours' strip, when it has the room beside the weather, in its own pixels.
HOURS_HEIGHT = 100


class HdLandscapeBoardLayout(HdVariantLayout):
    """The stops as a departure board under a line with the time; the weather, energy and Spotify along the foot."""

    layout_id = "board"
    canvases = frozenset({HD_LANDSCAPE})

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        # Spotify's now page is wide only with the strip's room to itself.
        panels = self._hd_panels(panels, wide_spotify=not self.SHOWS_ENERGY)
        weather = panels[ClientClasses.WEATHER.api_name]
        arrivals = panels[ClientClasses.TFL.api_name]
        header = BoardHeaderPanel(*weather_parts(weather), squares=_squares(arrivals))
        self._place_scaled(img, header, (0, 0), (canvas.width // SCALE, HEADER_HEIGHT), clock_format)

        x, width = MARGIN, canvas.width - 2 * MARGIN
        top = self._draw_banner(img, panels, (x, width), SCALE * HEADER_HEIGHT + HEADER_GAP, clock_format)
        area = self._spotify_area(panels, STRIP_HEIGHT) if self.SHOWS_SPOTIFY else []
        strip_height = max(STRIP_HEIGHT, sum(height for _panel, height in area) + GAP * (len(area) - 1))
        strip_top = canvas.height - EDGE - SCALE * strip_height
        self._draw_strip(img, panels, area, (x, strip_top, width, strip_height), clock_format)
        self._draw_board(img, stops_of(arrivals), (x, top, width, strip_top - STRIP_GAP - top), clock_format)
        return Frame(img)

    def _draw_banner(
        self, img: Image.Image, panels: dict[str, Panel], column: tuple[int, int], top: int, clock_format: ClockFormat
    ) -> int:
        """Draw the most important notice on a black line from `top`, if any; return where the board starts."""
        notices = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if not isinstance(notices, NoticeBoardPanel):
            return top
        banner = NoticeBannerPanel.of(notices)
        height = banner.fitted_height(NoticeBannerPanel.HEIGHT)
        if not height:
            return top
        x, width = column
        self._place_scaled(img, banner, (x, top), (width // SCALE, height), clock_format)
        return top + SCALE * height + BANNER_GAP

    def _draw_board(
        self, img: Image.Image, stops: list[Panel], box: tuple[int, int, int, int], clock_format: ClockFormat
    ) -> None:
        """Draw the stops side by side in `box` (left, top, width, height): each a departure board, or what it says.

        A message in the stops' place (none configured, say) has the whole box.
        """
        x, y, width, height = box
        boards = [stop for stop in stops if isinstance(stop, AbstractArrivalPanel)]
        if len(boards) < len(stops):
            self._place_scaled(img, stops[0], (x, y), (width // SCALE, height // SCALE), clock_format)
            return
        if not boards:
            return
        each = (width - COLUMN_GAP * (len(boards) - 1)) // len(boards)
        for stop in boards:
            size = (int(each / BOARD_SCALE), int(height / BOARD_SCALE))
            self._place_scaled(img, BoardStopPanel(stop), (x, y), size, clock_format, BOARD_SCALE)
            x += each + COLUMN_GAP

    def _draw_strip(
        self,
        img: Image.Image,
        panels: dict[str, Panel],
        area: list[tuple[Panel, int]],
        box: tuple[int, int, int, int],
        clock_format: ClockFormat,
    ) -> None:
        """Draw the strip along the foot in `box` (left, top, width, and height in its own pixels).

        On the left the weather now, and beside it the energy chart and Spotify's `area` (as _spotify_area has it) as
        this variant shows them; with neither, the coming hours. Each is STRIP_HEIGHT tall, at the strip's foot: the
        strip is taller only for the developer panel, above Spotify.
        """
        x, top, width, height = box
        bottom = top + SCALE * height
        foot = bottom - SCALE * STRIP_HEIGHT
        weather = panels[ClientClasses.WEATHER.api_name]
        now = BoardWeatherPanel(*weather_parts(weather))
        self._place_scaled(img, now, (x, foot), (WEATHER_WIDTH, STRIP_HEIGHT), clock_format)

        beside_x = x + SCALE * WEATHER_WIDTH + WEATHER_GAP
        beside_width = x + width - beside_x
        if self.SHOWS_ENERGY:
            energy_width = beside_width // SCALE
            if area:
                energy_width = round(energy_width * ENERGY_SHARE)
                spotify_x = beside_x + SCALE * (energy_width + 3 * GAP)
                self._draw_area(img, area, (spotify_x, top, x + width - spotify_x), clock_format)
            energy = panels[ClientClasses.GLOWMARKT.api_name]
            self._place_scaled(img, energy, (beside_x, foot), (energy_width, STRIP_HEIGHT), clock_format)
        elif area:
            self._draw_area(img, area, (beside_x, top, beside_width), clock_format)
        else:
            hours = HdHoursPanel(coming_hours(weather))
            if not hours.fitted_height(HOURS_HEIGHT):
                return
            hours_top = foot + SCALE * (STRIP_HEIGHT - HOURS_HEIGHT) // 2
            self._place_scaled(img, hours, (beside_x, hours_top), (beside_width // SCALE, HOURS_HEIGHT), clock_format)
        rule = beside_x - WEATHER_GAP // 2
        ImageDraw.Draw(img).line((rule, top, rule, bottom), fill=(0, 0, 0, 255), width=2)

    def _draw_area(
        self, img: Image.Image, area: list[tuple[Panel, int]], at: tuple[int, int, int], clock_format: ClockFormat
    ) -> None:
        """Draw Spotify's `area` top down from `at` (left, top, width): the developer panel's strip, then Spotify."""
        x, y, width = at
        for panel, height in area:
            self._place_scaled(img, panel, (x, y), (width // SCALE, height), clock_format)
            y += SCALE * (height + GAP)


def _squares(arrivals: Panel) -> tuple[int, int] | None:
    """Return which page of the stops is on screen and how many there are, when they don't all fit; else None."""
    if isinstance(arrivals, CombinedArrivalPanel) and arrivals.rotation is not None:
        return arrivals.rotation.page, arrivals.rotation.pages
    return None


HD_LANDSCAPE_BOARD_LAYOUTS = variants(HdLandscapeBoardLayout)
