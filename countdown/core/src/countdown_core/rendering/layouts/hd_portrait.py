"""The classic portrait dashboard on a 1872x1404 HD screen hung portrait (1404x1872).

The panels draw in pixels sized for the 7.5" screens, so at this screen's density (about 227 dpi) they'd be a third
as big. So each is drawn smaller and its pixels multiplied: the weather, Spotify and the notices twice, the stops three
times, so the departures read from across the room and fill their room. The weather gets more height than on portrait,
and draws itself bigger in it.
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.layouts.classic_portrait import (
    SPOTIFY_HEIGHT,
    BasePortraitLayout,
    ClassicPortraitLayout,
    GlowPortraitLayout,
    SpotifyGlowPortraitLayout,
    SpotifyPortraitLayout,
)
from countdown_core.rendering.screen import Canvas
from countdown_core.utils.utils import GAP, WEATHER_GAP
from countdown_core.weather.weather_panel import WeatherPanel
from countdown_core.weather.weather_trmnl_panel import WeatherTrmnlPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

HD_PORTRAIT = Canvas(1404, 1872)
# How many screen pixels each of a panel's takes, either way: most panels, and the stops.
SCALE = 2
ARRIVALS_SCALE = 3
# In the panels' own pixels (at SCALE): the full weather, taller than portrait's 270, and the compact weather with the
# energy chart beside it, taller than portrait's 185.
WEATHER_HEIGHT = 330
ENERGY_ROW_HEIGHT = 230
# The most the notices may take (at SCALE), as on portrait; the stops get the rest.
NOTICES_ROOM = 190


class HdPortraitLayout(ClassicPortraitLayout):
    """The portrait layout's order, top to bottom, with each panel scaled up; the variants say what shows."""

    canvases = frozenset({HD_PORTRAIT})

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        gap = SCALE * GAP
        width = canvas.width - 2 * gap

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
            bottom -= gap
        for panel, height in reversed(self._spotify_area(panels, SPOTIFY_HEIGHT) if self.SHOWS_SPOTIFY else []):
            bottom -= SCALE * height
            self._place_scaled(img, panel, (gap, bottom), (width // SCALE, height), clock_format)
            bottom -= gap

        arrivals_size = (width // ARRIVALS_SCALE, (bottom - arrivals_y) // ARRIVALS_SCALE)
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
            if isinstance(weather_panel, WeatherPanel):
                weather_panel = WeatherTrmnlPanel(weather_panel.weather)
            # It sizes its own fonts from its room, so it's drawn at the screen's own pixels: crisp, not doubled.
            self._place(img, weather_panel, (gap, gap), (width, SCALE * WEATHER_HEIGHT), clock_format=clock_format)
            return SCALE * WEATHER_HEIGHT

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

    @staticmethod
    def _place_scaled(  # noqa: PLR0913, PLR0917 -- _place's arguments and the scale
        img: Image.Image,
        panel: Panel,
        at: tuple[int, int],
        size: tuple[int, int],
        clock_format: ClockFormat,
        scale: int = SCALE,
    ) -> None:
        """Render `panel` `size` big in its own pixels, multiply them by `scale`, and paste it with its top left `at`.

        Each pixel is repeated, not smoothed: the 1-bit text keeps its edges, with no grey for the e-paper to dither.
        """
        rendered = panel.render(*size, clock_format=clock_format)
        scaled = rendered.resize((rendered.width * scale, rendered.height * scale), Image.Resampling.NEAREST)
        img.paste(scaled, at, scaled)


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
