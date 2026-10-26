"""The HD screen hung landscape (1872x1404) in three columns over the notices: the "column" layout.

Down the left, the date and the time, the weather now big and the coming hours (weather.column_weather_panel). In the
middle, the stops one under the other, at about the size the sidebar layout draws them. Down the right, Spotify's
now page with the developer panel under it, and the energy chart in the rest. Along the foot, across all three, the
notices in two columns (notices.column_notices_panel), only as tall as their rows.

Without the energy chart there's no right column: the stops take its width as well, side by side, with Spotify's wide
now page in a row under them (as the sidebar layout has it); with neither Spotify nor the chart, the stops have it all.
Without Spotify, the chart has the right column to itself. The rest are drawn twice as big (see layouts.hd).
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.api_registry import ClientClasses
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.notices.column_notices_panel import TwoColumnNoticeBoardPanel
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.layouts.hd import SCALE, stops_of, variants, weather_parts
from countdown_core.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_core.rendering.layouts.hd_portrait_designs import HdDesignLayout
from countdown_core.tfl.combined_arrival_panel import COLUMN_GAP, STOP_WIDTH
from countdown_core.weather.column_weather_panel import ColumnWeatherPanel

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.core.panel import Panel
    from countdown_core.rendering.screen import Canvas
    from countdown_core.utils.clock import ClockFormat

# In screen pixels: the room either side of what's drawn, above and below it, between the columns, and between the
# parts of a column.
MARGIN = 48
EDGE = 30
GUTTER = 48
SECTION_GAP = 30
# The weather column's width, in its own pixels; the right column's, in screen pixels.
WEATHER_WIDTH = 240
RIGHT_WIDTH = 600
# Where the middle column starts, and the right one, in screen pixels.
MIDDLE_X = MARGIN + SCALE * WEATHER_WIDTH + GUTTER
RIGHT_X = HD_LANDSCAPE.width - MARGIN - RIGHT_WIDTH
# How many screen pixels each of the stops' takes, either way, side by side across the middle and the right: two of them
# filling the width. One under the other in the middle column, they're drawn as the portrait designs draw them.
WIDE_SCALE = (HD_LANDSCAPE.width - MARGIN - MIDDLE_X) / (2 * STOP_WIDTH + COLUMN_GAP)
# The rest, in their own pixels (at SCALE): Spotify's now page in the right column; and under the stops without the
# right column, Spotify's wide now page (see HdVariantLayout._draw_bottom_row). The notices take what the portrait
# designs let them (HdDesignLayout._draw_notices).
SPOTIFY_HEIGHT = 130
WIDE_SPOTIFY_HEIGHT = 150
BOTTOM_ROW = (SPOTIFY_HEIGHT, WIDE_SPOTIFY_HEIGHT, WIDE_SPOTIFY_HEIGHT)


class HdLandscapeColumnLayout(HdDesignLayout):
    """The weather down the left, the stops in the middle, Spotify and the energy down the right; the notices under."""

    layout_id = "column"
    canvases = frozenset({HD_LANDSCAPE})

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        # Spotify's now page is wide only in the row under the stops, without the right column.
        panels = _two_column_notices(self._hd_panels(panels, wide_spotify=not self.SHOWS_ENERGY))
        bottom = self._draw_notices(
            img, panels, (MARGIN, canvas.width - 2 * MARGIN), canvas.height - EDGE, clock_format
        )

        height = (bottom - EDGE) // SCALE
        weather = ColumnWeatherPanel(*weather_parts(panels[ClientClasses.WEATHER.api_name]))
        self._place_scaled(img, weather, (MARGIN, EDGE), (WEATHER_WIDTH, height), clock_format)

        stops = panels[ClientClasses.TFL.api_name]
        if self.SHOWS_ENERGY:
            self._draw_right(img, panels, bottom, clock_format)
            box = (MIDDLE_X, EDGE, RIGHT_X - GUTTER - MIDDLE_X, bottom - EDGE)
            self._draw_stacked(img, stops_of(stops), box, clock_format)
            return Frame(img)

        width = canvas.width - MARGIN - MIDDLE_X
        if self.SHOWS_SPOTIFY:
            bottom = self._draw_bottom_row(img, panels, (MIDDLE_X, width), bottom, clock_format, BOTTOM_ROW)
            bottom -= SECTION_GAP
        size = (int(width / WIDE_SCALE), int((bottom - EDGE) / WIDE_SCALE))
        self._place_scaled(img, stops, (MIDDLE_X, EDGE), size, clock_format, WIDE_SCALE)
        return Frame(img)

    def _draw_right(self, img: Image.Image, panels: dict[str, Panel], bottom: int, clock_format: ClockFormat) -> None:
        """Draw the right column, down to `bottom`: Spotify's now page, the developer panel, and the energy chart.

        Spotify (if this variant shows it) first and the developer panel's strip under it, as far down as they need;
        the chart has the rest. The developer panel in Spotify's place is its strip too.
        """
        logical_width = RIGHT_WIDTH // SCALE
        y = EDGE
        spotify = panels[ClientClasses.SPOTIFY.api_name]
        if isinstance(spotify, DeveloperPanel):
            # In Spotify's place (no song playing), the whole developer panel needs more width than the column has.
            panels = panels | {ClientClasses.SPOTIFY.api_name: spotify.compacted()}
        if self.SHOWS_SPOTIFY:
            # Spotify's area has the developer panel first, as a strip above it: here it goes under the music.
            for panel, height in reversed(self._spotify_area(panels, SPOTIFY_HEIGHT)):
                self._place_scaled(img, panel, (RIGHT_X, y), (logical_width, height), clock_format)
                y += SCALE * height + SECTION_GAP
        energy = panels[ClientClasses.GLOWMARKT.api_name]
        self._place_scaled(img, energy, (RIGHT_X, y), (logical_width, (bottom - y) // SCALE), clock_format)


def _two_column_notices(panels: dict[str, Panel]) -> dict[str, Panel]:
    """Return `panels` with the notice board in two columns (a message in its place is left as it is)."""
    notices = panels.get(ClientClasses.NOTICE_BOARD.api_name)
    if not isinstance(notices, NoticeBoardPanel):
        return panels
    return panels | {ClientClasses.NOTICE_BOARD.api_name: TwoColumnNoticeBoardPanel(notices.notices, notices.style)}


HD_LANDSCAPE_COLUMN_LAYOUTS = variants(HdLandscapeColumnLayout)
