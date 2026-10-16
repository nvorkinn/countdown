"""The classic dashboard on a screen hung portrait (480x800), top to bottom: the weather, two
stops side by side, Spotify, and the notices, only as tall as their rows so the stops get the
rest. Without Spotify the stops and notices share its room. There's no energy chart: Glowmarkt
is left out on portrait.
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.rendering.layout import Frame, Layout
from countdown_core.rendering.screen import Canvas
from countdown_core.utils.utils import GAP
from countdown_core.weather.weather_panel import WeatherPanel
from countdown_core.weather.weather_trmnl_panel import HEIGHT as WEATHER_HEIGHT
from countdown_core.weather.weather_trmnl_panel import WeatherTrmnlPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

SPOTIFY_HEIGHT = 120


class ClassicPortraitLayout(Layout):
    layout_id = "classic"
    canvases = frozenset({Canvas(480, 800)})

    # The arrivals' height when the notices fill all the room they may have.
    ARRIVALS_HEIGHT = 185
    SHOWS_SPOTIFY = True

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        width = canvas.width - 2 * GAP

        weather_panel = panels[ClientClasses.WEATHER.api_name]
        if isinstance(weather_panel, WeatherPanel):
            weather_panel = WeatherTrmnlPanel(weather_panel.weather)
        self._place(img, weather_panel, GAP, GAP, width, WEATHER_HEIGHT, clock_format=clock_format)

        arrivals_y = GAP + WEATHER_HEIGHT + GAP
        spotify_room = SPOTIFY_HEIGHT + GAP if self.SHOWS_SPOTIFY else 0
        notices_room = canvas.height - GAP - spotify_room - (arrivals_y + self.ARRIVALS_HEIGHT + GAP)
        notices_height, spare = self._fit_notices(panels, notices_room)

        arrival_panel = panels[ClientClasses.TFL.api_name]
        arrivals = self._place(
            img,
            arrival_panel,
            GAP,
            arrivals_y,
            width,
            self.ARRIVALS_HEIGHT + spare,
            clock_format=clock_format,
        )
        arrivals_box = (GAP, arrivals_y, GAP + arrivals.size[0], arrivals_y + arrivals.size[1])

        bottom = canvas.height - GAP
        if notices_height:
            bottom -= notices_height
            self._place(
                img,
                panels[ClientClasses.NOTICE_BOARD.api_name],
                GAP,
                bottom,
                width,
                notices_height,
                clock_format=clock_format,
            )
            bottom -= GAP
        if self.SHOWS_SPOTIFY:
            self._place(
                img,
                panels[ClientClasses.SPOTIFY.api_name],
                GAP,
                bottom - SPOTIFY_HEIGHT,
                width,
                SPOTIFY_HEIGHT,
                clock_format=clock_format,
            )
        return Frame(img, arrivals_box)


class SpotifyPortraitLayout(ClassicPortraitLayout):
    requires = frozenset({ClientClasses.SPOTIFY})


class BasePortraitLayout(ClassicPortraitLayout):
    """No Spotify: its room goes to the stops (more departures each) and the notices."""

    excludes = frozenset({ClientClasses.SPOTIFY})
    SHOWS_SPOTIFY = False
    ARRIVALS_HEIGHT = 185 + 60


CLASSIC_PORTRAIT = [SpotifyPortraitLayout(), BasePortraitLayout()]
