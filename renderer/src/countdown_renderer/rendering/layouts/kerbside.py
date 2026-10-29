"""The kerbside layouts: the weather in brief over one stop, both big, so they read from across the room.

Nothing else is drawn: not Spotify, the notices or the energy chart. The weather (weather.kerbside_weather_panel) is
the now page's icon and temperature, the conditions, and the high, low and chance of rain. The stop is drawn in one of
three styles (tfl.kerbside_panel), each a layout of its own the config's `screen.layout` picks: track, ledger or
queue. With several stops configured, the layout shows one at a time and the departures client pages through them.

For the 7.5" screen hung portrait (480x800), and the HD screen hung portrait, where the same design is drawn 2.6
times as big (see utils.scaled_draw).
"""

from typing import TYPE_CHECKING, ClassVar, override

from PIL import Image

from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.rendering.layout import Frame
from countdown_renderer.rendering.layouts.hd import HdLayout, stops_of
from countdown_renderer.rendering.layouts.hd_portrait import HD_PORTRAIT
from countdown_renderer.rendering.screen import Canvas
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_renderer.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_renderer.tfl.kerbside_panel import KerbsidePanel
from countdown_renderer.weather.kerbside_weather_panel import HEIGHT as WEATHER_HEIGHT
from countdown_renderer.weather.kerbside_weather_panel import KerbsideWeatherPanel
from countdown_renderer.weather.weather_panel import WeatherPanel
from countdown_shared.weather.models import WeatherPeriod

if TYPE_CHECKING:
    from countdown_renderer.panel import Panel
    from countdown_renderer.tfl.kerbside_panel import KerbsideStyle
    from countdown_renderer.utils.clock import ClockFormat
    from countdown_shared.weather.models import Weather

PORTRAIT = Canvas(480, 800)
# How many screen pixels each of the panels' takes on each canvas, either way.
SCALES: dict[Canvas, float] = {PORTRAIT: 1, HD_PORTRAIT: 2.6}
# In the panels' own pixels: the room around what's drawn, and between the weather and the stop.
MARGIN = 14
SECTION_GAP = 14


class KerbsideLayout(HdLayout):
    """What the three kerbside layouts share: the weather over the stop; the style is the subclass's."""

    canvases = frozenset(SCALES)
    stops_shown = 1
    STYLE: ClassVar[KerbsideStyle]

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        scale = SCALES[canvas]
        width = round(canvas.width / scale) - 2 * MARGIN

        weather = panels[ClientClasses.WEATHER.api_name]
        if isinstance(weather, WeatherPanel):
            weather = KerbsideWeatherPanel(self._now(weather))
        at = (round(MARGIN * scale), round(MARGIN * scale))
        self._place_scaled(img, weather, at, (width, WEATHER_HEIGHT), clock_format, scale)

        stop_y = MARGIN + WEATHER_HEIGHT + SECTION_GAP
        height = round(canvas.height / scale) - MARGIN - stop_y
        stop = self._stop(panels[ClientClasses.TFL.api_name])
        if stop is not None:
            at = (round(MARGIN * scale), round(stop_y * scale))
            self._place_scaled(img, stop, at, (width, height), clock_format, scale)
        return Frame(img)

    @staticmethod
    def _now(weather: WeatherPanel) -> Weather:
        """Return the now page's readings: the carousel's first, whichever page the classic layouts are on."""
        return next((page for page in weather.pages if page.period is WeatherPeriod.NOW), weather.weather)

    def _stop(self, departures: Panel) -> Panel | None:
        """Return the stop to draw in this layout's style: the first on screen, or the message in the stops' place."""
        stops = stops_of(departures)
        if not stops:
            return None
        stop = stops[0]
        if not isinstance(stop, AbstractArrivalPanel):
            return stop
        rotation = departures.rotation if isinstance(departures, CombinedArrivalPanel) else None
        return KerbsidePanel(stop, self.STYLE, rotation)


class TrackLayout(KerbsideLayout):
    """Each route with its departures along a line of the next half hour."""

    layout_id = "track"
    STYLE = "track"


class LedgerLayout(KerbsideLayout):
    """Each route once, with the times after the next under its destination."""

    layout_id = "ledger"
    STYLE = "ledger"


class QueueLayout(KerbsideLayout):
    """Every departure on a row of its own, the soonest first."""

    layout_id = "queue"
    STYLE = "queue"


KERBSIDE_LAYOUTS = [TrackLayout(), LedgerLayout(), QueueLayout()]
