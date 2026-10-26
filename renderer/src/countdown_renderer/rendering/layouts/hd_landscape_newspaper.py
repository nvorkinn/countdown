"""The HD screen hung landscape as a newspaper's front page, in a serif face (the newspaper package's panels).

Across the top, the masthead: the first stop's name in capitals, over a dateline with the date, the edition and the
time. Under it, columns side by side: each stop (the next departure big, then every other route with a dotted leader
to its time), the weather (the weather now boxed, its readings, then tonight and tomorrow) and "In brief" (the notices,
then what's playing on Spotify and the developer's summary). Along the foot, the energy chart.

Without Glowmarkt, the columns run to the foot; without Spotify, the notices have its room in "In brief". There's room
for two stops: one alone has both stops' width.
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.developer.developer_panel import DeveloperPanel
from countdown_renderer.newspaper.brief_panel import NewspaperBriefPanel
from countdown_renderer.newspaper.energy_panel import NewspaperEnergyPanel
from countdown_renderer.newspaper.masthead_panel import HEIGHT as MASTHEAD_HEIGHT
from countdown_renderer.newspaper.masthead_panel import NewspaperMastheadPanel
from countdown_renderer.newspaper.stop_panel import NewspaperStopPanel
from countdown_renderer.newspaper.weather_panel import NewspaperWeatherPanel
from countdown_renderer.rendering.layout import Frame
from countdown_renderer.rendering.layouts.hd import SCALE, HdVariantLayout, stops_of, variants, weather_parts
from countdown_renderer.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel

if TYPE_CHECKING:
    from countdown_renderer.panel import Panel
    from countdown_renderer.rendering.screen import Canvas
    from countdown_renderer.utils.clock import ClockFormat

# In screen pixels: the room either side of the page and under it; between the masthead and the columns, and between
# the columns and the foot.
MARGIN = 56
SECTION_GAP = 26
# The columns' widths in their own pixels (at SCALE), and between them: the stops share theirs (one alone has it all),
# then the weather's; "In brief" has what's left.
STOPS_WIDTH = 492
WEATHER_WIDTH = 174
COLUMN_GAP = 20
STOPS = 2
# The foot's height, the energy chart's, in its own pixels.
FOOT_HEIGHT = 122


class HdLandscapeNewspaperLayout(HdVariantLayout):
    """A front page: the masthead, a column per stop, the weather and the news in brief, and energy at the foot."""

    layout_id = "newspaper"
    canvases = frozenset({HD_LANDSCAPE})

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        stops = stops_of(panels[ClientClasses.TFL.api_name])[:STOPS]
        place = next((stop.stop_name for stop in stops if isinstance(stop, AbstractArrivalPanel)), None)
        masthead = NewspaperMastheadPanel(place)
        self._place_scaled(img, masthead, (0, 0), (canvas.width // SCALE, MASTHEAD_HEIGHT), clock_format)

        page_width = (canvas.width - 2 * MARGIN) // SCALE
        bottom = canvas.height - MARGIN
        if self.SHOWS_ENERGY:
            bottom -= SCALE * FOOT_HEIGHT
            foot = NewspaperEnergyPanel(panels[ClientClasses.GLOWMARKT.api_name])
            self._place_scaled(img, foot, (MARGIN, bottom), (page_width, FOOT_HEIGHT), clock_format)
            bottom -= SECTION_GAP

        top = SCALE * MASTHEAD_HEIGHT + SECTION_GAP
        height = (bottom - top) // SCALE
        x = MARGIN
        if stops:
            each = (STOPS_WIDTH - COLUMN_GAP * (len(stops) - 1)) // len(stops)
            for stop in stops:
                self._place_scaled(img, NewspaperStopPanel(stop), (x, top), (each, height), clock_format)
                x += SCALE * (each + COLUMN_GAP)
        else:
            x += SCALE * (STOPS_WIDTH + COLUMN_GAP)

        weather = NewspaperWeatherPanel(*weather_parts(panels[ClientClasses.WEATHER.api_name]))
        self._place_scaled(img, weather, (x, top), (WEATHER_WIDTH, height), clock_format)
        x += SCALE * (WEATHER_WIDTH + COLUMN_GAP)

        brief = self._brief(panels)
        self._place_scaled(img, brief, (x, top), ((canvas.width - MARGIN - x) // SCALE, height), clock_format)
        return Frame(img)

    def _brief(self, panels: dict[str, Panel]) -> NewspaperBriefPanel:
        """Return "In brief": the notices, and what's playing and the developer's summary if this variant shows them.

        The developer panel is under its own name while Spotify plays a song, else in Spotify's place
        (rendering.place_developer_panel).
        """
        notices = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if not self.SHOWS_SPOTIFY:
            return NewspaperBriefPanel(notices)
        spotify = panels.get(ClientClasses.SPOTIFY.api_name)
        developer = next(
            (
                panel
                for panel in (panels.get(ClientClasses.DEVELOPER.api_name), spotify)
                if isinstance(panel, DeveloperPanel)
            ),
            None,
        )
        return NewspaperBriefPanel(notices, spotify, developer)


# The first is what's drawn if no variant fits (never, while the four cover every combination).
HD_LANDSCAPE_NEWSPAPER_LAYOUTS = variants(HdLandscapeNewspaperLayout)
