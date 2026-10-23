"""The dashboard on a 1872x1404 HD screen hung landscape: a black sidebar, and everything else in a column beside it.

Down the left edge, the time and the weather, white on black (weather.hd_sidebar_panel). Beside it, top to bottom: the
two stops side by side, two and a quarter times as big (just filling the column's width), the coming hours, the energy
chart and Spotify side by side, and the notices, only as tall as their rows. The stops get whatever the rest leave.
Without Glowmarkt, Spotify has its row to itself and its now page is wide, as on the HD portrait screen; without
Spotify, the energy chart has it; with neither, the stops get it. The rest are drawn twice as big (see layouts.hd).
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.layouts.hd import SCALE, HdVariantLayout, coming_hours, weather_parts
from countdown_core.rendering.screen import Canvas
from countdown_core.tfl.combined_arrival_panel import COLUMN_GAP, STOP_WIDTH
from countdown_core.weather.hd_sidebar_panel import HdHoursPanel, HdSidebarPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

HD_LANDSCAPE = Canvas(1872, 1404)
# In screen pixels: the sidebar's width, the room either side of the column beside it, and above and below it.
SIDEBAR_WIDTH = 540
MARGIN = 55
EDGE = 30
# Between the column's parts, in screen pixels.
SECTION_GAP = 30
# How many screen pixels each of the stops' takes, either way: two stops and the gap between them fill the column.
ARRIVALS_SCALE = (HD_LANDSCAPE.width - SIDEBAR_WIDTH - 2 * MARGIN) / (2 * STOP_WIDTH + COLUMN_GAP)
# The rest, in their own pixels (at SCALE): the hours' strip; Spotify beside the energy chart, and on its own (its
# wide now page); the energy chart on its own; the most the notices may take.
HOURS_HEIGHT = 100
SPOTIFY_HEIGHT = 120
WIDE_SPOTIFY_HEIGHT = 150
ENERGY_HEIGHT = 150
NOTICES_ROOM = 120


class HdLandscapeLayout(HdVariantLayout):
    """The sidebar, and the column beside it; the variants say whether Spotify and the energy chart show."""

    layout_id = "sidebar"
    canvases = frozenset({HD_LANDSCAPE})

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        # Spotify's now page is wide only with the row to itself.
        panels = self._hd_panels(panels, wide_spotify=not self.SHOWS_ENERGY)
        weather = panels[ClientClasses.WEATHER.api_name]
        self._place_scaled(
            img, _sidebar(weather), (0, 0), (SIDEBAR_WIDTH // SCALE, canvas.height // SCALE), clock_format
        )

        x = SIDEBAR_WIDTH + MARGIN
        width = canvas.width - x - MARGIN
        bottom = canvas.height - EDGE
        notices_height, _ = self._fit_notices(panels, NOTICES_ROOM)
        if notices_height:
            bottom -= SCALE * notices_height
            notices = panels[ClientClasses.NOTICE_BOARD.api_name]
            self._place_scaled(img, notices, (x, bottom), (width // SCALE, notices_height), clock_format)
            bottom -= SECTION_GAP
        if self.SHOWS_SPOTIFY or self.SHOWS_ENERGY:
            heights = (SPOTIFY_HEIGHT, WIDE_SPOTIFY_HEIGHT, ENERGY_HEIGHT)
            bottom = self._draw_bottom_row(img, panels, (x, width), bottom, clock_format, heights) - SECTION_GAP
        hours = HdHoursPanel(coming_hours(weather))
        if hours.fitted_height(HOURS_HEIGHT):
            bottom -= SCALE * HOURS_HEIGHT
            self._place_scaled(img, hours, (x, bottom), (width // SCALE, HOURS_HEIGHT), clock_format)
            bottom -= SECTION_GAP

        arrivals_size = (int(width / ARRIVALS_SCALE), int((bottom - EDGE) / ARRIVALS_SCALE))
        arrivals = panels[ClientClasses.TFL.api_name]
        self._place_scaled(img, arrivals, (x, EDGE), arrivals_size, clock_format, ARRIVALS_SCALE)
        return Frame(img)


def _sidebar(weather: Panel) -> HdSidebarPanel:
    """Return the sidebar for what the weather client made: a forecast's pages, or a message saying why there's none."""
    return HdSidebarPanel(*weather_parts(weather))


class SpotifyGlowHdLandscapeLayout(HdLandscapeLayout):
    """Everything: the energy chart and Spotify side by side under the hours."""

    requires = frozenset({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT})


class SpotifyHdLandscapeLayout(HdLandscapeLayout):
    """Spotify's now page wide, in the row to itself (Glowmarkt isn't set up)."""

    requires = frozenset({ClientClasses.SPOTIFY})
    excludes = frozenset({ClientClasses.GLOWMARKT})
    SHOWS_ENERGY = False


class GlowHdLandscapeLayout(HdLandscapeLayout):
    """No Spotify: the energy chart has the row to itself."""

    requires = frozenset({ClientClasses.GLOWMARKT})
    excludes = frozenset({ClientClasses.SPOTIFY})
    SHOWS_SPOTIFY = False


class BaseHdLandscapeLayout(HdLandscapeLayout):
    """Neither: the row goes to the stops."""

    excludes = frozenset({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT})
    SHOWS_SPOTIFY = False
    SHOWS_ENERGY = False


# The first is what's drawn if no variant fits (never, while the four cover every combination).
HD_LANDSCAPE_LAYOUTS = [
    SpotifyGlowHdLandscapeLayout(),
    SpotifyHdLandscapeLayout(),
    GlowHdLandscapeLayout(),
    BaseHdLandscapeLayout(),
]
