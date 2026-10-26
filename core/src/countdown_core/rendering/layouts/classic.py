"""The dashboard on an 800x480 screen hung landscape.

The arrivals and weather go along the top, and below them whichever of Spotify, the notices and the energy chart the
device can show. One variant per combination of Spotify and Glowmarkt.
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.rendering.layout import Box, Frame, Layout
from countdown_core.rendering.screen import Canvas
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.utils.utils import GAP, WEATHER_GAP

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

# Where every variant puts the arrivals panel.
ARRIVALS_ORIGIN = (5, 5)
# The arrivals panel is as wide as its stops; a message in its place gets one stop's width.
ARRIVALS_MESSAGE_WIDTH = 262

SPOTIFY_WIDTH = 536
SPOTIFY_HEIGHT = 120
# Without Spotify (not set up, or nothing playing), a second row of stops, if the notices leave the stops this tall.
SECOND_ROW_FROM = 300


class ClassicLayout(Layout):
    """The classic layout's common ground, laid out bottom up in the column below the arrivals.

    That column is the whole width without the energy chart. The notices go at the bottom, only as tall as their
    rows, then Spotify. The arrivals take the rest, so fewer notices mean more departures.
    """

    layout_id = "classic"
    canvases = frozenset({Canvas(800, 480)})

    # The arrivals' height when the notices fill all the room they may have.
    ARRIVALS_HEIGHT = 185
    SHOWS_SPOTIFY = False
    # The energy chart goes under the weather, which then keeps its height while the
    # arrivals grow beside it.
    SHOWS_ENERGY = False

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        spotify_area, notices_height, arrivals_height = self._heights(panels, canvas)
        arrivals_box = self._draw_top_row(img, panels, canvas, arrivals_height, clock_format)

        width = self._column_width(canvas, arrivals_box)
        bottom = canvas.height - GAP
        if notices_height:
            bottom -= notices_height
            self._place(
                img,
                panels[ClientClasses.NOTICE_BOARD.api_name],
                (GAP, bottom),
                (width, notices_height),
                clock_format=clock_format,
            )
            bottom -= GAP
        for panel, height in reversed(spotify_area):
            bottom -= height
            self._place(img, panel, (GAP, bottom), (width, height), clock_format=clock_format)
            bottom -= GAP
        if self.SHOWS_ENERGY:
            x, y = GAP + width + GAP, GAP + self.ARRIVALS_HEIGHT + GAP
            energy_panel = panels[ClientClasses.GLOWMARKT.api_name]
            self._place(
                img,
                energy_panel,
                (x, y),
                (canvas.width - x - GAP, canvas.height - y - GAP),
                clock_format=clock_format,
            )
        return Frame(img)

    def _heights(self, panels: dict[str, Panel], canvas: Canvas) -> tuple[list[tuple[Panel, int]], int, int]:
        """Return what's drawn in Spotify's area with each one's height, the notices' height, and the arrivals'."""
        spotify_area = self._spotify_area(panels, SPOTIFY_HEIGHT) if self.SHOWS_SPOTIFY else []
        spotify_room = sum(height + GAP for _, height in spotify_area)
        notices_room = canvas.height - self.ARRIVALS_HEIGHT - 3 * GAP - spotify_room
        notices_height, spare = self._fit_notices(panels, notices_room)
        return spotify_area, notices_height, self.ARRIVALS_HEIGHT + spare

    @override
    def _arrivals_height(self, panels: dict[str, Panel], canvas: Canvas) -> int:
        return self._heights(panels, canvas)[2]

    def _column_width(self, canvas: Canvas, arrivals_box: Box) -> int:  # noqa: ARG002 -- variants size it by the arrivals
        """How wide Spotify and the notices are drawn."""
        return canvas.width - 2 * GAP

    def _draw_top_row(
        self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas, height: int, clock_format: ClockFormat
    ) -> Box:
        """Draw the arrivals, `height` tall, with the weather beside them, and return the arrivals' box.

        They're at the same place in every variant.
        """
        arrival_panel = panels[ClientClasses.TFL.api_name]
        arrivals_width = canvas.width if isinstance(arrival_panel, CombinedArrivalPanel) else ARRIVALS_MESSAGE_WIDTH
        x, y = ARRIVALS_ORIGIN
        arrivals = self._place(img, arrival_panel, (x, y), (arrivals_width, height), clock_format=clock_format)
        arrivals_box = (x, y, x + arrivals.size[0], y + arrivals.size[1])

        weather_x = arrivals_box[2] + WEATHER_GAP
        weather_height = self.ARRIVALS_HEIGHT if self.SHOWS_ENERGY else height
        weather_panel = panels[ClientClasses.WEATHER.api_name]
        self._place(
            img,
            weather_panel,
            (weather_x, y),
            (canvas.width - weather_x - GAP, weather_height),
            clock_format=clock_format,
        )
        return arrivals_box


class SpotifyGlowLayout(ClassicLayout):
    """Everything: the notices below Spotify on the left, the energy chart on the right."""

    requires = frozenset({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT})
    SHOWS_SPOTIFY = True
    SHOWS_ENERGY = True

    @override
    def _column_width(self, canvas: Canvas, arrivals_box: Box) -> int:
        return SPOTIFY_WIDTH


class SpotifyLayout(ClassicLayout):
    """No energy chart (Glowmarkt isn't set up).

    Spotify and the notices both take the full width below the arrivals and weather, so notices get longer lines.
    """

    requires = frozenset({ClientClasses.SPOTIFY})
    excludes = frozenset({ClientClasses.GLOWMARKT})
    SHOWS_SPOTIFY = True


class GlowLayout(ClassicLayout):
    """No Spotify: two columns below the weather and the top of the arrivals.

    The notices go under the arrivals, the energy chart under the weather.
    """

    requires = frozenset({ClientClasses.GLOWMARKT})
    excludes = frozenset({ClientClasses.SPOTIFY})
    SHOWS_ENERGY = True
    stops_shown = 4
    TWO_ROWS_HEIGHT = SECOND_ROW_FROM

    @override
    def _column_width(self, canvas: Canvas, arrivals_box: Box) -> int:
        return arrivals_box[2] - arrivals_box[0]


class BaseLayout(ClassicLayout):
    """Neither Spotify nor Glowmarkt.

    Four stops, a 2x2 grid beside the weather, if the notices leave room for it, and the notices full width below.
    """

    excludes = frozenset({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT})
    stops_shown = 4
    TWO_ROWS_HEIGHT = SECOND_ROW_FROM


# The first is what's drawn if no variant fits (never, while the four cover every combination).
CLASSIC = [SpotifyGlowLayout(), SpotifyLayout(), GlowLayout(), BaseLayout()]
