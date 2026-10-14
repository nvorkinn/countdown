"""The classic dashboard on a screen hung portrait (480x800), top to bottom: the weather, two
stops side by side, the notices, and Spotify. Without Spotify the stops and notices share its
room. There's no energy chart: Glowmarkt is left out on portrait."""

from typing import override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.rendering.layout import Frame, Layout
from countdown_core.rendering.screen import Canvas
from countdown_core.utils.utils import GAP
from countdown_core.weather.weather_panel import WeatherPanel
from countdown_core.weather.weather_trmnl_panel import HEIGHT as WEATHER_HEIGHT
from countdown_core.weather.weather_trmnl_panel import WeatherTrmnlPanel

SPOTIFY_HEIGHT = 120


class ClassicPortraitLayout(Layout):
    layout_id = "classic"
    canvases = frozenset({Canvas(480, 800)})

    ARRIVALS_HEIGHT = 185
    SHOWS_SPOTIFY = True

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas) -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        width = canvas.width - 2 * GAP

        weather_panel = panels[ClientClasses.WEATHER.api_name]
        if isinstance(weather_panel, WeatherPanel):
            weather_panel = WeatherTrmnlPanel(weather_panel.weather)
        self._place(img, weather_panel, GAP, GAP, width, WEATHER_HEIGHT)

        arrivals_y = GAP + WEATHER_HEIGHT + GAP
        arrivals = self._place(img, panels[ClientClasses.TFL.api_name], GAP, arrivals_y, width, self.ARRIVALS_HEIGHT)
        arrivals_box = (GAP, arrivals_y, GAP + arrivals.size[0], arrivals_y + arrivals.size[1])

        # The slots stay put however much the arrivals draw, so the notices never move.
        notices_y = arrivals_y + self.ARRIVALS_HEIGHT + GAP
        notices_bottom = canvas.height - GAP
        if self.SHOWS_SPOTIFY:
            spotify_y = canvas.height - SPOTIFY_HEIGHT - GAP
            self._place(img, panels[ClientClasses.SPOTIFY.api_name], GAP, spotify_y, width, SPOTIFY_HEIGHT)
            notices_bottom = spotify_y - GAP

        notices_panel = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if notices_panel is not None:
            self._place(img, notices_panel, GAP, notices_y, width, notices_bottom - notices_y)
        return Frame(img, arrivals_box)


class SpotifyPortraitLayout(ClassicPortraitLayout):
    requires = frozenset({ClientClasses.SPOTIFY})


class BasePortraitLayout(ClassicPortraitLayout):
    """No Spotify: its room goes to the stops (more departures each) and the notices."""

    excludes = frozenset({ClientClasses.SPOTIFY})
    SHOWS_SPOTIFY = False
    ARRIVALS_HEIGHT = 185 + 60


CLASSIC_PORTRAIT = [SpotifyPortraitLayout(), BasePortraitLayout()]
