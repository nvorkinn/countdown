"""The classic portrait dashboard on a 1872x1404 HD screen hung portrait (1404x1872).

Each panel is drawn bigger, at the screen's own resolution (see layouts.hd): the stops two and a half times as big, so
the departures read from across the room and fill their room, and the rest twice. The full weather is the HD screen's
own (weather.hd_weather_panel), taller than on portrait, in the config's design, at the stops' scale so its text is
their size. Spotify's now page is wide (its style's `wide` page), what's up next in a cell
of its own, and the notice board a table (notices.HdNoticeBoardPanel).
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.rendering.layout import Frame
from countdown_renderer.rendering.layouts.classic_portrait import (
    BasePortraitLayout,
    ClassicPortraitLayout,
    GlowPortraitLayout,
    SpotifyGlowPortraitLayout,
    SpotifyPortraitLayout,
)
from countdown_renderer.rendering.layouts.hd import SCALE, HdLayout
from countdown_renderer.rendering.screen import Canvas
from countdown_renderer.utils.utils import GAP, WEATHER_GAP
from countdown_renderer.weather.hd_weather_panel import HdWeatherPanel
from countdown_renderer.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from countdown_renderer.panel import Panel
    from countdown_renderer.utils.clock import ClockFormat

HD_PORTRAIT = Canvas(1404, 1872)
# How many screen pixels each of the stops' takes, either way (the rest take SCALE).
ARRIVALS_SCALE = 2.5
# The full weather's height, in screen pixels: taller than portrait's 270 doubled.
WEATHER_HEIGHT = 660
# The compact weather with the energy chart beside it, in their own pixels (at SCALE): taller than portrait's 185.
ENERGY_ROW_HEIGHT = 230
# Spotify's height (at SCALE): taller than portrait's 120, as its now page is wide with what's up next beside it.
SPOTIFY_HEIGHT = 150
# The most the notices may take (at SCALE), as on portrait; the stops get the rest.
NOTICES_ROOM = 190
# Without Spotify (not set up, or nothing playing), the notices may take more, and the stops have a second row if
# the notices leave them this tall (in their own pixels, at ARRIVALS_SCALE).
NO_SPOTIFY_NOTICES_ROOM = 300
SECOND_ROW_FROM = 250
# Between Spotify and the notices (at SCALE): more than the usual gap, so the board's header is set apart.
NOTICES_GAP = 3 * GAP


class HdPortraitLayout(HdLayout, ClassicPortraitLayout):
    """The portrait layout's order, top to bottom, with each panel scaled up; the variants say what shows."""

    canvases = frozenset({HD_PORTRAIT})

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        gap = SCALE * GAP
        width = canvas.width - 2 * gap
        panels = self._hd_panels(panels)

        arrivals_y = gap + self._draw_hd_top_row(img, panels, width, clock_format) + SCALE * WEATHER_GAP
        below, arrivals_bottom = self._below_stops(panels, canvas)
        for panel, y, height in below:
            self._place_scaled(img, panel, (gap, y), (width // SCALE, height), clock_format)

        arrivals_size = (int(width / ARRIVALS_SCALE), int((arrivals_bottom - arrivals_y) / ARRIVALS_SCALE))
        self._place_scaled(
            img, panels[ClientClasses.TFL.api_name], (gap, arrivals_y), arrivals_size, clock_format, ARRIVALS_SCALE
        )
        return Frame(img)

    def _below_stops(self, panels: dict[str, Panel], canvas: Canvas) -> tuple[list[tuple[Panel, int, int]], int]:
        """Return what goes below the stops, bottom up, and where the stops end, in screen pixels.

        Each panel below comes with its top (in screen pixels) and height (in its own). `panels` as _hd_panels returns
        them.
        """
        gap = SCALE * GAP
        bottom = canvas.height - gap
        below = []
        notices_room = NOTICES_ROOM if self.SHOWS_SPOTIFY else NO_SPOTIFY_NOTICES_ROOM
        notices_height, _ = self._fit_notices(panels, notices_room)
        if notices_height:
            bottom -= SCALE * notices_height
            below.append((panels[ClientClasses.NOTICE_BOARD.api_name], bottom, notices_height))
            bottom -= SCALE * NOTICES_GAP
        for panel, height in reversed(self._spotify_area(panels, SPOTIFY_HEIGHT) if self.SHOWS_SPOTIFY else []):
            bottom -= SCALE * height
            below.append((panel, bottom, height))
            bottom -= gap
        return below, bottom

    @override
    def _arrivals_height(self, panels: dict[str, Panel], canvas: Canvas) -> int:
        arrivals_y = SCALE * GAP + self._hd_top_row_height() + SCALE * WEATHER_GAP
        _, bottom = self._below_stops(self._hd_panels(panels), canvas)
        return int((bottom - arrivals_y) / ARRIVALS_SCALE)

    def _hd_top_row_height(self) -> int:
        """Return the weather's row's height in screen pixels, as _draw_hd_top_row draws it."""
        return SCALE * ENERGY_ROW_HEIGHT if self.SHOWS_ENERGY else WEATHER_HEIGHT

    def _draw_hd_top_row(
        self, img: Image.Image, panels: dict[str, Panel], width: int, clock_format: ClockFormat
    ) -> int:
        """Draw the weather, and the energy chart beside it if this variant shows one; return the row's height."""
        gap = SCALE * GAP
        weather_panel = panels[ClientClasses.WEATHER.api_name]
        logical_width = width // SCALE
        if not self.SHOWS_ENERGY:
            # The HD screen's own weather, at the stops' scale; a message (no weather) has its pixels doubled.
            scale = SCALE
            if isinstance(weather_panel, WeatherPanel):
                weather_panel = HdWeatherPanel(weather_panel.weather, weather_panel.design, weather_panel.pages)
                scale = ARRIVALS_SCALE
            size = (int(width / scale), int(WEATHER_HEIGHT / scale))
            self._place_scaled(img, weather_panel, (gap, gap), size, clock_format, scale)
            return WEATHER_HEIGHT

        weather_width = (logical_width - WEATHER_GAP) // 2
        self._place_scaled(img, weather_panel, (gap, gap), (weather_width, ENERGY_ROW_HEIGHT), clock_format)
        energy_x = gap + SCALE * (weather_width + WEATHER_GAP)
        self._place_scaled(
            img,
            panels[ClientClasses.GLOWMARKT.api_name],
            (energy_x, gap),
            ((gap + width - energy_x) // SCALE, ENERGY_ROW_HEIGHT),
            clock_format,
        )
        return SCALE * ENERGY_ROW_HEIGHT


class SpotifyGlowHdPortraitLayout(HdPortraitLayout, SpotifyGlowPortraitLayout):
    """Everything: the energy chart beside the compact weather, Spotify between the stops and the notices."""


class SpotifyHdPortraitLayout(HdPortraitLayout, SpotifyPortraitLayout):
    """With Spotify, and the full weather (Glowmarkt isn't set up)."""


class GlowHdPortraitLayout(HdPortraitLayout, GlowPortraitLayout):
    """No Spotify: the energy chart beside the compact weather, and Spotify's room goes to the notices and the stops."""

    TWO_ROWS_HEIGHT = SECOND_ROW_FROM


class BaseHdPortraitLayout(HdPortraitLayout, BasePortraitLayout):
    """Neither: the full weather, and Spotify's room goes to the notices and the stops."""

    TWO_ROWS_HEIGHT = SECOND_ROW_FROM


# The first is what's drawn if no variant fits (never, while the four cover every combination).
HD_PORTRAIT_LAYOUTS = [
    SpotifyGlowHdPortraitLayout(),
    SpotifyHdPortraitLayout(),
    GlowHdPortraitLayout(),
    BaseHdPortraitLayout(),
]
