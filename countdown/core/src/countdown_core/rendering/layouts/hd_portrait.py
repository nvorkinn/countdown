"""The classic portrait dashboard on a 1872x1404 HD screen hung portrait (1404x1872).

Each panel is drawn bigger, at the screen's own resolution (see layouts.hd): the stops two and a half times as big, so
the departures read from across the room and fill their room, and the rest twice. The full weather is the HD screen's
own (weather.hd_weather_panel), taller than on portrait, in the config's design, at the stops' scale so its text is
their size. Spotify's now page is wide (its style's `wide` page), what's up next in a cell
of its own, and the notice board a table (notices.HdNoticeBoardPanel).
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.layouts.classic_portrait import (
    BasePortraitLayout,
    ClassicPortraitLayout,
    GlowPortraitLayout,
    SpotifyGlowPortraitLayout,
    SpotifyPortraitLayout,
)
from countdown_core.rendering.layouts.hd import SCALE, HdLayout
from countdown_core.rendering.screen import Canvas
from countdown_core.utils.utils import GAP, WEATHER_GAP
from countdown_core.weather.hd_weather_panel import HdWeatherPanel
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

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
        bottom = canvas.height - gap
        notices_height, _ = self._fit_notices(panels, NOTICES_ROOM)
        if notices_height:
            bottom -= SCALE * notices_height
            self._place_scaled(
                img,
                panels[ClientClasses.NOTICE_BOARD.api_name],
                (gap, bottom),
                (width // SCALE, notices_height),
                clock_format,
            )
            bottom -= SCALE * NOTICES_GAP
        for panel, height in reversed(self._spotify_area(panels, SPOTIFY_HEIGHT) if self.SHOWS_SPOTIFY else []):
            bottom -= SCALE * height
            self._place_scaled(img, panel, (gap, bottom), (width // SCALE, height), clock_format)
            bottom -= gap

        arrivals_size = (int(width / ARRIVALS_SCALE), int((bottom - arrivals_y) / ARRIVALS_SCALE))
        self._place_scaled(
            img, panels[ClientClasses.TFL.api_name], (gap, arrivals_y), arrivals_size, clock_format, ARRIVALS_SCALE
        )
        return Frame(img)

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
    """No Spotify: the energy chart beside the compact weather, and Spotify's room goes to the stops."""


class BaseHdPortraitLayout(HdPortraitLayout, BasePortraitLayout):
    """Neither: the full weather, and Spotify's room goes to the stops."""


# The first is what's drawn if no variant fits (never, while the four cover every combination).
HD_PORTRAIT_LAYOUTS = [
    SpotifyGlowHdPortraitLayout(),
    SpotifyHdPortraitLayout(),
    GlowHdPortraitLayout(),
    BaseHdPortraitLayout(),
]
