"""The HD screen's other portrait layouts (1404x1872), each a different take on the same panels.

The classic one (layouts.hd_portrait) is the default; the config's `screen.layout` picks one of these instead:

- masthead: the time and the weather in a black band across the top, the stops side by side under it, the coming
  hours, the energy chart beside Spotify, and the notices.
- column: the time, the weather now and the coming hours down the left edge; the stops one under the other beside it,
  then Spotify and the energy chart; the notices along the foot.
- lockscreen: the time big in the middle, with the weather under it; then the stops, Spotify, the energy chart with
  the next few hours, and the notices, each in a card of its own.
- timeline: the time and the weather on a line; the worst notice as a black banner; the next half hour's departures
  along a line of time (tfl.timeline_panel); the coming hours, and the energy chart beside Spotify.
- board: the time and the weather on a line; the stops as a departures board, each route once with its next three
  times (tfl.route_board_panel); the coming hours, the energy chart beside Spotify, and the notices.
- pages: two pages that take turns each minute. Going out: the stops one under the other, the notices, the coming
  hours. At home: the weather in the config's design, the coming hours, the energy chart and Spotify.

Each has four variants, for whether Spotify and the energy chart show (see layouts.hd.variants); what one doesn't
show leaves its room to the stops (on the home page, to the weather). The panels are drawn twice as big, the stops a
little more (see layouts.hd).
"""

from typing import TYPE_CHECKING, override

from PIL import Image, ImageDraw

from countdown_core.i18n import _
from countdown_renderer import rotation
from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.notices.notice_board_panel import NoticeBannerPanel, NoticeBoardPanel
from countdown_renderer.rendering.layout import Frame
from countdown_renderer.rendering.layouts.hd import (
    SCALE,
    HdVariantLayout,
    coming_hours,
    stops_of,
    variants,
    weather_parts,
)
from countdown_renderer.rendering.layouts.hd_portrait import HD_PORTRAIT
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_renderer.tfl.combined_arrival_panel import COLUMN_GAP, STOP_WIDTH
from countdown_renderer.tfl.route_board_panel import RouteBoardPanel
from countdown_renderer.tfl.timeline_panel import TimelinePanel
from countdown_renderer.weather.hd_clock_panels import (
    CENTRED_HEADER_HEIGHT,
    HEADER_HEIGHT,
    MASTHEAD_HEIGHT,
    HdClockPanel,
    HdMastheadPanel,
    HdWeatherColumnPanel,
)
from countdown_renderer.weather.hd_sidebar_panel import HdHoursPanel
from countdown_renderer.weather.hd_weather_panel import HdWeatherPanel
from countdown_renderer.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from countdown_renderer.panel import Panel
    from countdown_renderer.rendering.screen import Canvas
    from countdown_renderer.utils.clock import ClockFormat

# In screen pixels: the room either side of what's drawn, and above and below it; and between its parts.
MARGIN = 48
EDGE = 30
SECTION_GAP = 30
# How many screen pixels each of the stops' takes, either way: side by side, two stops and the gap between them fill
# the screen's width; one under the other, a little less, so a stop's text is about the size it is side by side.
ARRIVALS_SCALE = (HD_PORTRAIT.width - 2 * MARGIN) / (2 * STOP_WIDTH + COLUMN_GAP)
STACKED_SCALE = 2.25
# In the lock screen's cards, a little smaller still, so each stop shows more than its next departure.
CARD_SCALE = 2
# The rest, in their own pixels (at SCALE): the hours' strip; the most the notices may take.
HOURS_HEIGHT = 90
NOTICES_ROOM = 120
# The weather column's width, and in it Spotify's height and the energy chart's beside the stops.
COLUMN_WIDTH = 240
COLUMN_SPOTIFY_HEIGHT = 120
COLUMN_ENERGY_HEIGHT = 130
# The lock screen's cards: their corners' radius, their outline, the room inside them and between them (screen
# pixels); the music card's height and the widgets' (their own); how many hours its widget shows beside the chart.
CARD_RADIUS = 24
CARD_OUTLINE = 3
CARD_PADDING = 20
CARD_GAP = 24
CARD_SPOTIFY_HEIGHT = 120
WIDGETS_HEIGHT = 95
WIDGET_HOURS = 3
# The pages layout's home page: the energy chart's height on its own row, and Spotify's (its wide now page).
HOME_ENERGY_HEIGHT = 170
HOME_SPOTIFY_HEIGHT = 150
# The home page's weather, at the stops' scale, at most this tall in screen pixels.
HOME_WEATHER_SCALE = 2.5
HOME_WEATHER_HEIGHT = 760


class HdDesignLayout(HdVariantLayout):
    """What the HD portrait designs share: the strips they stack from the foot up, and the stops one under the other."""

    canvases = frozenset({HD_PORTRAIT})

    @staticmethod
    def _column(canvas: Canvas) -> tuple[int, int]:
        """Return the left edge and width of what's drawn between the margins."""
        return MARGIN, canvas.width - 2 * MARGIN

    def _draw_notices(
        self,
        img: Image.Image,
        panels: dict[str, Panel],
        column: tuple[int, int],
        bottom: int,
        clock_format: ClockFormat,
    ) -> int:
        """Draw the notices, only as tall as their rows, ending at `bottom`; return where what's above them ends."""
        height, _spare = self._fit_notices(panels, NOTICES_ROOM)
        if not height:
            return bottom
        x, width = column
        top = bottom - SCALE * height
        self._place_scaled(
            img, panels[ClientClasses.NOTICE_BOARD.api_name], (x, top), (width // SCALE, height), clock_format
        )
        return top - SECTION_GAP

    def _draw_hours(  # noqa: PLR0913, PLR0917 -- the frame, the weather, where, and the clock
        self,
        img: Image.Image,
        weather: Panel,
        column: tuple[int, int],
        bottom: int,
        clock_format: ClockFormat,
        hours: int | None = None,
    ) -> int:
        """Draw the coming hours (the first `hours` of them, if given) ending at `bottom`; return where above ends."""
        panel = HdHoursPanel(coming_hours(weather)[:hours])
        if not panel.fitted_height(HOURS_HEIGHT):
            return bottom
        x, width = column
        top = bottom - SCALE * HOURS_HEIGHT
        self._place_scaled(img, panel, (x, top), (width // SCALE, HOURS_HEIGHT), clock_format)
        return top - SECTION_GAP

    def _draw_stacked(
        self,
        img: Image.Image,
        stops: Sequence[Panel],
        box: tuple[int, int, int, int],
        clock_format: ClockFormat,
        *,
        cards: bool = False,
    ) -> None:
        """Draw the stops one under the other, sharing `box` (left, top, width, height), each in a card if `cards`.

        A stop in the card style draws its own card, so it isn't drawn in another.
        """
        x, y, width, height = box
        if not stops:
            return
        gap = CARD_GAP if cards else SECTION_GAP
        each = (height - gap * (len(stops) - 1)) // len(stops)
        for stop in stops:
            inner = (x, y, width, each)
            if cards and not (isinstance(stop, AbstractArrivalPanel) and stop.style == "card"):
                inner = _card(img, (x, y, width, each))
            stop_x, stop_y, stop_width, stop_height = inner
            scale = CARD_SCALE if cards else STACKED_SCALE
            size = (int(stop_width / scale), int(stop_height / scale))
            self._place_scaled(img, stop, (stop_x, stop_y), size, clock_format, scale)
            y += each + gap

    @staticmethod
    def _header(
        panels: dict[str, Panel],
        *,
        centred: bool = False,
        summary: str | None = None,
        squares: tuple[int, int] | None = None,
    ) -> HdClockPanel:
        """Return the clock line over the rest, with the weather client's pages (or message); see HdClockPanel."""
        pages, message = weather_parts(panels[ClientClasses.WEATHER.api_name])
        return HdClockPanel(pages, message, centred=centred, summary=summary, squares=squares)

    @staticmethod
    def _new(canvas: Canvas) -> Image.Image:
        return Image.new("RGBA", canvas.size, (255, 255, 255, 255))


def _card(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Draw a card's outline around `box` (left, top, width, height) and return the room inside it, likewise."""
    x, y, width, height = box
    ImageDraw.Draw(img).rounded_rectangle(
        (x, y, x + width - 1, y + height - 1), CARD_RADIUS, outline=(0, 0, 0, 255), width=CARD_OUTLINE
    )
    return x + CARD_PADDING, y + CARD_PADDING, width - 2 * CARD_PADDING, height - 2 * CARD_PADDING


class HdMastheadLayout(HdDesignLayout):
    """A black band across the top with the time and the weather; the stops side by side under it, then the rest."""

    layout_id = "masthead"

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        panels = self._hd_panels(panels, wide_spotify=not self.SHOWS_ENERGY)
        weather = panels[ClientClasses.WEATHER.api_name]
        band = HdMastheadPanel(*weather_parts(weather))
        self._place_scaled(img, band, (0, 0), (canvas.width // SCALE, MASTHEAD_HEIGHT), clock_format)

        column = self._column(canvas)
        bottom = self._draw_notices(img, panels, column, canvas.height - EDGE, clock_format)
        bottom = self._draw_bottom_row(img, panels, column, bottom, clock_format)
        if self.SHOWS_SPOTIFY or self.SHOWS_ENERGY:
            bottom -= SECTION_GAP
        bottom = self._draw_hours(img, weather, column, bottom, clock_format)

        x, width = column
        top = SCALE * MASTHEAD_HEIGHT + SECTION_GAP
        size = (int(width / ARRIVALS_SCALE), int((bottom - top) / ARRIVALS_SCALE))
        self._place_scaled(img, panels[ClientClasses.TFL.api_name], (x, top), size, clock_format, ARRIVALS_SCALE)
        return Frame(img)


class HdColumnLayout(HdDesignLayout):
    """The time and the weather down the left edge; the stops one under the other beside it, then Spotify and energy."""

    layout_id = "column"

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        panels = self._hd_panels(panels, wide_spotify=False)
        weather = panels[ClientClasses.WEATHER.api_name]
        bottom = self._draw_notices(img, panels, self._column(canvas), canvas.height - EDGE, clock_format)

        column = HdWeatherColumnPanel(*weather_parts(weather))
        self._place_scaled(img, column, (0, EDGE), (COLUMN_WIDTH, (bottom - EDGE) // SCALE), clock_format)
        rule = SCALE * COLUMN_WIDTH + SECTION_GAP // 2
        ImageDraw.Draw(img).line((rule, EDGE, rule, bottom), fill=(0, 0, 0, 255), width=2)

        x = SCALE * COLUMN_WIDTH + SECTION_GAP
        width = canvas.width - MARGIN - x
        if self.SHOWS_ENERGY:
            bottom -= SCALE * COLUMN_ENERGY_HEIGHT
            energy = panels[ClientClasses.GLOWMARKT.api_name]
            self._place_scaled(img, energy, (x, bottom), (width // SCALE, COLUMN_ENERGY_HEIGHT), clock_format)
            bottom -= SECTION_GAP
        if self.SHOWS_SPOTIFY:
            for panel, height in reversed(self._spotify_area(panels, COLUMN_SPOTIFY_HEIGHT)):
                bottom -= SCALE * height
                self._place_scaled(img, panel, (x, bottom), (width // SCALE, height), clock_format)
                bottom -= SECTION_GAP

        self._draw_stacked(
            img, stops_of(panels[ClientClasses.TFL.api_name]), (x, EDGE, width, bottom - EDGE), clock_format
        )
        return Frame(img)


class HdLockScreenLayout(HdDesignLayout):
    """The time big in the middle, the weather under it; then each stop, Spotify, the widgets and notices in cards."""

    layout_id = "lockscreen"

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        # Spotify's narrow now page: the wide one's queue would crowd out the stops.
        panels = self._hd_panels(panels, wide_spotify=False)
        header = self._header(panels, centred=True)
        self._place_scaled(img, header, (0, 0), (canvas.width // SCALE, CENTRED_HEADER_HEIGHT), clock_format)

        x, width = self._column(canvas)
        inner_width = width - 2 * CARD_PADDING
        bottom = canvas.height - EDGE
        notices_height, _spare = self._fit_notices(panels, NOTICES_ROOM)
        if notices_height:
            card_height = SCALE * notices_height + 2 * CARD_PADDING
            bottom -= card_height
            inner_x, inner_y, _width, _height = _card(img, (x, bottom, width, card_height))
            notices = panels[ClientClasses.NOTICE_BOARD.api_name]
            self._place_scaled(img, notices, (inner_x, inner_y), (inner_width // SCALE, notices_height), clock_format)
            bottom -= CARD_GAP
        bottom = self._draw_widgets(img, panels, (x, width), bottom, clock_format)
        if self.SHOWS_SPOTIFY:
            area = self._spotify_area(panels, CARD_SPOTIFY_HEIGHT)
            card_height = SCALE * sum(height for _panel, height in area) + SECTION_GAP * (len(area) - 1)
            bottom -= card_height + 2 * CARD_PADDING
            inner_x, y, _width, _height = _card(img, (x, bottom, width, card_height + 2 * CARD_PADDING))
            for panel, height in area:
                self._place_scaled(img, panel, (inner_x, y), (inner_width // SCALE, height), clock_format)
                y += SCALE * height + SECTION_GAP
            bottom -= CARD_GAP

        top = SCALE * CENTRED_HEADER_HEIGHT
        self._draw_stacked(
            img, stops_of(panels[ClientClasses.TFL.api_name]), (x, top, width, bottom - top), clock_format, cards=True
        )
        return Frame(img)

    def _draw_widgets(
        self,
        img: Image.Image,
        panels: dict[str, Panel],
        column: tuple[int, int],
        bottom: int,
        clock_format: ClockFormat,
    ) -> int:
        """Draw the energy chart (if this variant shows it) and the next few hours in cards side by side.

        Without the energy chart, the hours card has the row, and every coming hour. Returns where above them ends.
        """
        hours = HdHoursPanel(coming_hours(panels[ClientClasses.WEATHER.api_name]))
        widgets: list[Panel] = []
        if self.SHOWS_ENERGY:
            widgets.append(panels[ClientClasses.GLOWMARKT.api_name])
            hours = HdHoursPanel(hours.hours[:WIDGET_HOURS])
        if hours.fitted_height(WIDGETS_HEIGHT):
            widgets.append(hours)
        if not widgets:
            return bottom
        x, width = column
        card_height = SCALE * WIDGETS_HEIGHT + 2 * CARD_PADDING
        top = bottom - card_height
        # The chart gets the larger share, the hours the rest.
        shares = [0.62, 0.38] if len(widgets) == 2 else [1.0]  # noqa: PLR2004 -- the chart and the hours
        card_x = x
        for widget, share in zip(widgets, shares, strict=True):
            card_width = round((width - CARD_GAP * (len(widgets) - 1)) * share)
            inner_x, inner_y, inner_width, _height = _card(img, (card_x, top, card_width, card_height))
            self._place_scaled(img, widget, (inner_x, inner_y), (inner_width // SCALE, WIDGETS_HEIGHT), clock_format)
            card_x += card_width + CARD_GAP
        return top - CARD_GAP


class HdTimelineLayout(HdDesignLayout):
    """The time on a line, the worst notice as a banner, the next half hour along a line of time, then the rest."""

    layout_id = "timeline"

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        panels = self._hd_panels(panels, wide_spotify=not self.SHOWS_ENERGY)
        weather = panels[ClientClasses.WEATHER.api_name]
        self._place_scaled(img, self._header(panels), (0, 0), (canvas.width // SCALE, HEADER_HEIGHT), clock_format)
        column = self._column(canvas)
        x, width = column
        top = SCALE * HEADER_HEIGHT + EDGE // 2
        top = self._draw_banner(img, panels, column, top, clock_format)

        bottom = self._draw_bottom_row(img, panels, column, canvas.height - EDGE, clock_format)
        if self.SHOWS_SPOTIFY or self.SHOWS_ENERGY:
            bottom -= SECTION_GAP
        bottom = self._draw_hours(img, weather, column, bottom, clock_format)

        timeline = TimelinePanel(stops_of(panels[ClientClasses.TFL.api_name]))
        self._place_scaled(img, timeline, (x, top), (width // SCALE, (bottom - top) // SCALE), clock_format)
        return Frame(img)

    def _draw_banner(
        self, img: Image.Image, panels: dict[str, Panel], column: tuple[int, int], top: int, clock_format: ClockFormat
    ) -> int:
        """Draw the most important notice as a banner starting at `top`; return where what's under it starts."""
        notices = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        if not isinstance(notices, NoticeBoardPanel):
            return top + SECTION_GAP
        banner = NoticeBannerPanel.of(notices)
        height = banner.fitted_height(NoticeBannerPanel.HEIGHT)
        if not height:
            return top + SECTION_GAP
        x, width = column
        self._place_scaled(img, banner, (x, top), (width // SCALE, height), clock_format)
        return top + SCALE * height + SECTION_GAP


class HdBoardLayout(HdDesignLayout):
    """The time on a line; the stops as a departures board, each route with its next three times; then the rest."""

    layout_id = "board"

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        panels = self._hd_panels(panels, wide_spotify=not self.SHOWS_ENERGY)
        weather = panels[ClientClasses.WEATHER.api_name]
        self._place_scaled(img, self._header(panels), (0, 0), (canvas.width // SCALE, HEADER_HEIGHT), clock_format)
        column = self._column(canvas)
        x, width = column

        bottom = self._draw_notices(img, panels, column, canvas.height - EDGE, clock_format)
        bottom = self._draw_bottom_row(img, panels, column, bottom, clock_format)
        if self.SHOWS_SPOTIFY or self.SHOWS_ENERGY:
            bottom -= SECTION_GAP
        bottom = self._draw_hours(img, weather, column, bottom, clock_format)

        top = SCALE * HEADER_HEIGHT + SECTION_GAP
        board = RouteBoardPanel(stops_of(panels[ClientClasses.TFL.api_name]))
        self._place_scaled(img, board, (x, top), (width // SCALE, (bottom - top) // SCALE), clock_format)
        return Frame(img)


class HdPagesLayout(HdDesignLayout):
    """Two pages that take turns each minute: going out (the stops, the notices, the hours), and at home (the rest)."""

    layout_id = "pages"
    PAGES = 2

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        img = self._new(canvas)
        page = rotation.page(self.PAGES)
        if page == 0:
            self._draw_out(img, self._hd_panels(panels), canvas, clock_format)
        else:
            self._draw_home(img, self._hd_panels(panels), canvas, clock_format)
        return Frame(img)

    def _draw_out(self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas, clock_format: ClockFormat) -> None:
        """Draw the page for going out: the stops one under the other, the notices, and the coming hours."""
        header = self._header(panels, squares=(0, self.PAGES))
        self._place_scaled(img, header, (0, 0), (canvas.width // SCALE, HEADER_HEIGHT), clock_format)
        column = self._column(canvas)
        x, width = column
        bottom = self._draw_hours(
            img, panels[ClientClasses.WEATHER.api_name], column, canvas.height - EDGE, clock_format
        )
        bottom = self._draw_notices(img, panels, column, bottom, clock_format)
        top = SCALE * HEADER_HEIGHT + SECTION_GAP
        self._draw_stacked(
            img, stops_of(panels[ClientClasses.TFL.api_name]), (x, top, width, bottom - top), clock_format
        )

    def _draw_home(self, img: Image.Image, panels: dict[str, Panel], canvas: Canvas, clock_format: ClockFormat) -> None:
        """Draw the page for home: the next departures, the weather in the config's design, the hours and the rest."""
        # With nothing leaving, the header has the weather now instead.
        summary = next_departures(stops_of(panels[ClientClasses.TFL.api_name])) or None
        header = self._header(panels, squares=(1, self.PAGES), summary=summary)
        self._place_scaled(img, header, (0, 0), (canvas.width // SCALE, HEADER_HEIGHT), clock_format)
        column = self._column(canvas)
        x, width = column
        top = SCALE * HEADER_HEIGHT + SECTION_GAP

        bottom = canvas.height - EDGE
        if self.SHOWS_SPOTIFY:
            for panel, height in reversed(self._spotify_area(panels, HOME_SPOTIFY_HEIGHT)):
                bottom -= SCALE * height
                self._place_scaled(img, panel, (x, bottom), (width // SCALE, height), clock_format)
                bottom -= SECTION_GAP
        if self.SHOWS_ENERGY:
            bottom -= SCALE * HOME_ENERGY_HEIGHT
            energy = panels[ClientClasses.GLOWMARKT.api_name]
            self._place_scaled(img, energy, (x, bottom), (width // SCALE, HOME_ENERGY_HEIGHT), clock_format)
            bottom -= SECTION_GAP

        weather = panels[ClientClasses.WEATHER.api_name]
        if not isinstance(weather, WeatherPanel):
            # A message: its pixels doubled, as the other layouts draw it.
            self._place_scaled(img, weather, (x, top), (width // SCALE, (bottom - top) // SCALE), clock_format)
            return
        if weather.design != "timeline":
            # The timeline design has the hours already.
            bottom = self._draw_hours(img, weather, column, bottom, clock_format)
        height = min(HOME_WEATHER_HEIGHT, bottom - top)
        hd = HdWeatherPanel(weather.weather, weather.design, weather.pages)
        size = (int(width / HOME_WEATHER_SCALE), int(height / HOME_WEATHER_SCALE))
        self._place_scaled(img, hd, (x, top), size, clock_format, HOME_WEATHER_SCALE)


def next_departures(stops: Sequence[Panel]) -> str:
    """Return each stop's next departure in brief, e.g. "36 in 2 min   N in 1 min"."""
    parts = []
    for stop in stops:
        if not isinstance(stop, AbstractArrivalPanel) or not stop.departures:
            continue
        route = stop.departures[0]
        minutes = route.minutes[0]
        if minutes == 0:
            parts.append(_("{line} due").format(line=route.badge))
        else:
            parts.append(_("{line} in {minutes} min").format(line=route.badge, minutes=minutes))
    return "   ".join(parts)


HD_MASTHEAD_LAYOUTS = variants(HdMastheadLayout)
HD_COLUMN_LAYOUTS = variants(HdColumnLayout)
HD_LOCK_SCREEN_LAYOUTS = variants(HdLockScreenLayout)
HD_TIMELINE_LAYOUTS = variants(HdTimelineLayout)
HD_BOARD_LAYOUTS = variants(HdBoardLayout)
HD_PAGES_LAYOUTS = variants(HdPagesLayout)
# In the order the config page offers them, after the classic one.
HD_PORTRAIT_DESIGNS = (
    HD_MASTHEAD_LAYOUTS
    + HD_COLUMN_LAYOUTS
    + HD_LOCK_SCREEN_LAYOUTS
    + HD_TIMELINE_LAYOUTS
    + HD_BOARD_LAYOUTS
    + HD_PAGES_LAYOUTS
)
