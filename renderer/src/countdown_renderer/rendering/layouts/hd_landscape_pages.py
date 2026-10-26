"""The HD screen hung landscape (1872x1404) as two pages that take turns each minute: going out, and at home.

Each page has the clock on a line across the top, with a square per page over its middle, the one on screen filled.

- Going out: the weather now at the end of the clock's line; the two stops side by side under it, about the size the
  sidebar layout draws them; and the notices along the foot, only as tall as their rows.
- At home: each stop's next departure at the end of the clock's line; the weather big on the left (the weather now,
  the coming hours, its readings and the forecast's later pages, weather.pages_weather_panel); and down the right, the
  energy chart over Spotify's song, with the developer panel's strip under it.

The four variants (see layouts.hd.variants) change only the home page. Without the energy chart, the weather has the
width, with Spotify's wide now page across the foot; without Spotify, the energy chart has the right column; with
neither, the weather has the page.
"""

from typing import TYPE_CHECKING, override

from PIL import Image, ImageDraw

from countdown_renderer import rotation
from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.rendering.layout import Frame
from countdown_renderer.rendering.layouts.hd import SCALE, stops_of, variants, weather_parts
from countdown_renderer.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_renderer.rendering.layouts.hd_portrait_designs import HdDesignLayout, next_departures
from countdown_renderer.weather.hd_clock_panels import HEADER_HEIGHT
from countdown_renderer.weather.hd_clock_panels import MARGIN as HEADER_MARGIN
from countdown_renderer.weather.pages_weather_panel import WIDE_HEIGHT, HdPagesWeatherPanel

if TYPE_CHECKING:
    from countdown_renderer.panel import Panel
    from countdown_renderer.rendering.screen import Canvas
    from countdown_renderer.utils.clock import ClockFormat

# In screen pixels: the room either side of what's drawn, and under it; between its parts; and between the stops,
# and the home page's two columns (a rule down the middle of the gap).
MARGIN = 48
EDGE = 30
SECTION_GAP = 30
COLUMN_GAP = 80
# The clock's line is drawn a little smaller than the rest, as a header, so the stops have the room.
HEADER_SCALE = 1.5
# How many screen pixels each of a stop's takes, either way: about as big as the sidebar layout draws them.
STOP_SCALE = 2.25
# The home page's weather column, in screen pixels (the rest is the right column's); the right column's Spotify
# area, in its own pixels (at SCALE), beside the energy chart and across the foot (its wide now page).
WEATHER_WIDTH = 860
SPOTIFY_HEIGHT = 140
WIDE_SPOTIFY_HEIGHT = 150
# With the page's width, the weather is drawn bigger, up to this scale, as far as its height lets it keep the hours
# under it all (pages_weather_panel.WIDE_HEIGHT).
WIDE_WEATHER_SCALE = 2.5
# Going out, the notices may take this much (in their own pixels, at SCALE): they have the foot to themselves.
OUT_NOTICES_ROOM = 240


class HdLandscapePagesLayout(HdDesignLayout):
    """Two pages that take turns each minute: going out (the stops and the notices), and at home (the rest)."""

    layout_id = "pages"
    canvases = frozenset({HD_LANDSCAPE})
    PAGES = 2

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        if rotation.page(self.PAGES) == 0:
            self._draw_out(img, self._hd_panels(panels), canvas, clock_format)
        else:
            self._draw_home(img, self._hd_panels(panels, wide_spotify=not self.SHOWS_ENERGY), canvas, clock_format)
        return Frame(img)

    def _draw_header(
        self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas, clock_format: ClockFormat, page: int
    ) -> int:
        """Draw the clock's line for `page`; return where what's under it starts.

        Going out it has the weather now at its end; at home, each stop's next departure (the weather now if none).
        """
        summary = None
        if page == 1:
            summary = next_departures(stops_of(panels[ClientClasses.TFL.api_name])) or None
        header = self._header(panels, summary=summary, squares=(page, self.PAGES))
        # Shifted in, so the clock's left edge lines up with the rest's.
        inset = round(MARGIN - HEADER_SCALE * HEADER_MARGIN)
        size = (int((canvas.width - 2 * inset) / HEADER_SCALE), HEADER_HEIGHT)
        self._place_scaled(img, header, (inset, 0), size, clock_format, HEADER_SCALE)
        return round(HEADER_SCALE * HEADER_HEIGHT)

    def _draw_out(self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas, clock_format: ClockFormat) -> None:
        """Draw the page for going out: the stops side by side, and the notices along the foot."""
        top = self._draw_header(img, panels, canvas, clock_format, 0) + SECTION_GAP // 2
        x, width = MARGIN, canvas.width - 2 * MARGIN
        bottom = canvas.height - EDGE
        notices_height, _spare = self._fit_notices(panels, OUT_NOTICES_ROOM)
        if notices_height:
            bottom -= SCALE * notices_height
            notices = panels[ClientClasses.NOTICE_BOARD.api_name]
            self._place_scaled(img, notices, (x, bottom), (width // SCALE, notices_height), clock_format)
            bottom -= SECTION_GAP
        stops = stops_of(panels[ClientClasses.TFL.api_name])
        each = (width - COLUMN_GAP * (len(stops) - 1)) // len(stops)
        for stop in stops:
            size = (int(each / STOP_SCALE), int((bottom - top) / STOP_SCALE))
            self._place_scaled(img, stop, (x, top), size, clock_format, STOP_SCALE)
            x += each + COLUMN_GAP

    def _draw_home(self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas, clock_format: ClockFormat) -> None:
        """Draw the page for home: the weather big, and the energy chart and Spotify beside it or under it."""
        top = self._draw_header(img, panels, canvas, clock_format, 1) + SECTION_GAP
        bottom = canvas.height - EDGE
        x, width = MARGIN, canvas.width - 2 * MARGIN
        if self.SHOWS_ENERGY:
            right = x + WEATHER_WIDTH + COLUMN_GAP
            rule = x + WEATHER_WIDTH + COLUMN_GAP // 2
            ImageDraw.Draw(img).line((rule, top, rule, bottom), fill=(0, 0, 0, 255), width=3)
            self._draw_right(img, panels, (right, top, canvas.width - MARGIN - right, bottom - top), clock_format)
            width = WEATHER_WIDTH
        elif self.SHOWS_SPOTIFY:
            for panel, height in reversed(self._spotify_area(panels, WIDE_SPOTIFY_HEIGHT)):
                bottom -= SCALE * height
                self._place_scaled(img, panel, (x, bottom), (width // SCALE, height), clock_format)
                bottom -= SECTION_GAP
        self._draw_weather(img, panels, (x, top, width, bottom - top), clock_format)

    def _draw_right(
        self, img: Image.Image, panels: dict[str, Panel], box: tuple[int, int, int, int], clock_format: ClockFormat
    ) -> None:
        """Draw the energy chart in `box` (left, top, width, height), over Spotify's song and the developer's strip."""
        x, top, width, height = box
        bottom = top + height
        if self.SHOWS_SPOTIFY:
            # Spotify's song first, then the developer panel's strip under it.
            area = list(reversed(self._spotify_area(panels, SPOTIFY_HEIGHT)))
            y = bottom - sum(SCALE * panel_height for _panel, panel_height in area) - SECTION_GAP * (len(area) - 1)
            bottom = y - SECTION_GAP
            ImageDraw.Draw(img).line((x, bottom, x + width, bottom), fill=(0, 0, 0, 255), width=3)
            bottom -= SECTION_GAP
            for panel, panel_height in area:
                self._place_scaled(img, panel, (x, y), (width // SCALE, panel_height), clock_format)
                y += SCALE * panel_height + SECTION_GAP
        energy = panels[ClientClasses.GLOWMARKT.api_name]
        self._place_scaled(img, energy, (x, top), (width // SCALE, (bottom - top) // SCALE), clock_format)

    def _draw_weather(
        self, img: Image.Image, panels: dict[str, Panel], box: tuple[int, int, int, int], clock_format: ClockFormat
    ) -> None:
        """Draw the weather big in `box` (left, top, width, height): bigger still with the page's width to itself."""
        x, y, width, height = box
        weather = panels[ClientClasses.WEATHER.api_name]
        pages, _message = weather_parts(weather)
        if not pages:
            # A message: its pixels doubled, as the other layouts draw it.
            self._place_scaled(img, weather, (x, y), (width // SCALE, height // SCALE), clock_format)
            return
        scale = SCALE
        if width > WEATHER_WIDTH:
            scale = min(WIDE_WEATHER_SCALE, max(SCALE, height / WIDE_HEIGHT))
        size = (int(width / scale), int(height / scale))
        self._place_scaled(img, HdPagesWeatherPanel(pages), (x, y), size, clock_format, scale)


HD_LANDSCAPE_PAGES_LAYOUTS = variants(HdLandscapePagesLayout)
