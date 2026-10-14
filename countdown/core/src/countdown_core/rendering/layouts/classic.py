"""The dashboard as it has always looked on an 800x480 screen hung landscape: the arrivals and
weather along the top, and below them whichever of the notices, Spotify and the energy chart
the device can show. One variant per combination of Spotify and Glowmarkt."""

from abc import abstractmethod
from typing import override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.rendering.layout import Box, Frame, Layout
from countdown_core.rendering.screen import Canvas
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.utils.utils import GAP

# Where every variant puts the arrivals panel, the part of the screen that changes every
# minute and so the only part worth a partial refresh.
ARRIVALS_ORIGIN = (5, 5)
# The arrivals panel is as wide as its stops; a message in its place gets one stop's width.
ARRIVALS_MESSAGE_WIDTH = 262

SPOTIFY_WIDTH = 536
SPOTIFY_HEIGHT = 120


class ClassicLayout(Layout):
    layout_id = "classic"
    canvases = frozenset({Canvas(800, 480)})

    ARRIVALS_HEIGHT = 185

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas) -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        arrivals_box, below_top_row = self._draw_top_row(img, panels, canvas)
        self._draw_below(img, panels, canvas, arrivals_box, below_top_row)
        return Frame(img, arrivals_box)

    @abstractmethod
    def _draw_below(
        self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas, arrivals_box: Box, below_top_row: int
    ) -> None:
        """What goes under the arrivals and weather: the part the variants differ in."""

    def _draw_top_row(self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas) -> tuple[Box, int]:
        """The arrivals and weather, the same in every variant so the arrivals' partial
        refreshes keep working. Returns the arrivals' box and the y below the row."""
        arrival_panel = panels[ClientClasses.TFL.api_name]
        arrivals_width = canvas.width if isinstance(arrival_panel, CombinedArrivalPanel) else ARRIVALS_MESSAGE_WIDTH
        x, y = ARRIVALS_ORIGIN
        arrivals = self._place(img, arrival_panel, x, y, arrivals_width, self.ARRIVALS_HEIGHT)
        arrivals_box = (x, y, x + arrivals.size[0], y + arrivals.size[1])

        weather_x = arrivals_box[2] + GAP
        weather_panel = panels[ClientClasses.WEATHER.api_name]
        self._place(img, weather_panel, weather_x, y, canvas.width - weather_x - GAP, arrivals.size[1])
        return arrivals_box, arrivals_box[3] + GAP


class SpotifyGlowLayout(ClassicLayout):
    """Everything: the notices above Spotify on the left, the energy chart on the right."""

    requires = frozenset({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT})

    @override
    def _draw_below(self, img, panels, canvas, arrivals_box, below_top_row):
        spotify_y = canvas.height - SPOTIFY_HEIGHT - GAP
        spotify = self._place(
            img, panels[ClientClasses.SPOTIFY.api_name], GAP, spotify_y, SPOTIFY_WIDTH, SPOTIFY_HEIGHT
        )

        notices_panel = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if notices_panel is not None:
            self._place(img, notices_panel, GAP, below_top_row, spotify.size[0], spotify_y - GAP - below_top_row)

        energy_x = GAP + spotify.size[0] + GAP
        self._place(
            img,
            panels[ClientClasses.GLOWMARKT.api_name],
            energy_x,
            below_top_row,
            canvas.width - energy_x - GAP,
            canvas.height - below_top_row - GAP,
        )


class SpotifyLayout(ClassicLayout):
    """No energy chart (Glowmarkt isn't set up): the notices and Spotify both take the
    full width below the arrivals and weather, so notices get longer lines."""

    requires = frozenset({ClientClasses.SPOTIFY})
    excludes = frozenset({ClientClasses.GLOWMARKT})

    @override
    def _draw_below(self, img, panels, canvas, arrivals_box, below_top_row):
        width = canvas.width - 2 * GAP
        spotify_y = canvas.height - SPOTIFY_HEIGHT - GAP
        self._place(img, panels[ClientClasses.SPOTIFY.api_name], GAP, spotify_y, width, SPOTIFY_HEIGHT)

        notices_panel = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if notices_panel is not None:
            self._place(img, notices_panel, GAP, below_top_row, width, spotify_y - GAP - below_top_row)


class GlowLayout(ClassicLayout):
    """No Spotify: two columns below the top row -- the notices under the arrivals, the
    energy chart under the weather."""

    requires = frozenset({ClientClasses.GLOWMARKT})
    excludes = frozenset({ClientClasses.SPOTIFY})

    @override
    def _draw_below(self, img, panels, canvas, arrivals_box, below_top_row):
        notices_panel = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        notices_width = arrivals_box[2] - arrivals_box[0]
        if notices_panel is not None:
            height = canvas.height - below_top_row - GAP
            self._place(img, notices_panel, GAP, below_top_row, notices_width, height)

        energy_x = GAP + notices_width + GAP
        self._place(
            img,
            panels[ClientClasses.GLOWMARKT.api_name],
            energy_x,
            below_top_row,
            canvas.width - energy_x - GAP,
            canvas.height - below_top_row - GAP,
        )


class BaseLayout(ClassicLayout):
    """Neither Spotify nor Glowmarkt: four stops, a 2x2 grid beside the weather, and the
    notices full width below."""

    excludes = frozenset({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT})
    stops_shown = 4
    ARRIVALS_HEIGHT = 185 * 2

    @override
    def _draw_below(self, img, panels, canvas, arrivals_box, below_top_row):
        notices_panel = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if notices_panel is not None:
            width, height = canvas.width - 2 * GAP, canvas.height - below_top_row - GAP
            self._place(img, notices_panel, GAP, below_top_row, width, height)


# The first is what's drawn if no variant fits (never, while the four cover every combination).
CLASSIC = [SpotifyGlowLayout(), SpotifyLayout(), GlowLayout(), BaseLayout()]
