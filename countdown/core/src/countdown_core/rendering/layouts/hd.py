"""What the HD screen's layouts share: the panels it draws its own way, and drawing a panel bigger.

The panels are laid out in pixels sized for the 7.5" screens, so at the HD screen's density (about 227 dpi) they'd be a
third as big. So each is drawn bigger, at the screen's own resolution (see utils.scaled_draw). A panel that can't draw
itself bigger (a message) has its pixels repeated instead.
"""

from typing import TYPE_CHECKING, ClassVar

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.notices.notice_board_panel import HdNoticeBoardPanel, NoticeBoardPanel
from countdown_core.rendering.layout import Layout
from countdown_core.spotify.spotify_panel import HdSpotifyPanel, SpotifyPanel
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.utils.scaled_draw import drawing_at
from countdown_core.utils.utils import GAP
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat
    from countdown_core.weather.models import Weather, WeatherHour

# How many screen pixels each of a panel's takes, either way, unless the layout says otherwise.
SCALE = 2
# The energy chart's share of a row's width, with Spotify beside it.
ENERGY_SHARE = 0.45


class HdLayout(Layout):
    """A layout for the HD screen: its panels drawn bigger, Spotify's now page and the notice board its own way."""

    @staticmethod
    def _hd_panels(panels: dict[str, Panel], *, wide_spotify: bool = True) -> dict[str, Panel]:
        """Return `panels` with the notice board, and Spotify's now page if `wide_spotify`, drawn the HD screen's way.

        Anything else under their names (a message, a top list, the developer panel) is left as it is.
        """
        hd = dict(panels)
        spotify = hd.get(ClientClasses.SPOTIFY.api_name)
        if wide_spotify and isinstance(spotify, SpotifyPanel):
            hd[ClientClasses.SPOTIFY.api_name] = HdSpotifyPanel.of(spotify)
        notices = hd.get(ClientClasses.NOTICE_BOARD.api_name)
        if isinstance(notices, NoticeBoardPanel):
            hd[ClientClasses.NOTICE_BOARD.api_name] = HdNoticeBoardPanel.of(notices)
        return hd

    @staticmethod
    def _place_scaled(  # noqa: PLR0913, PLR0917 -- _place's arguments and the scale
        img: Image.Image,
        panel: Panel,
        at: tuple[int, int],
        size: tuple[int, int],
        clock_format: ClockFormat,
        scale: float = SCALE,
    ) -> None:
        """Render `panel` `size` big in its own pixels, `scale` times as big, and paste it with its top left `at`.

        A panel that `scales` draws itself at that size, crisply. Any other has its pixels repeated, not smoothed: the
        1-bit text keeps its edges, with no grey for the e-paper to dither.
        """
        if panel.scales:
            with drawing_at(scale):
                scaled = panel.render(*size, clock_format=clock_format)
        else:
            rendered = panel.render(*size, clock_format=clock_format)
            scaled = rendered.resize(
                (round(rendered.width * scale), round(rendered.height * scale)), Image.Resampling.NEAREST
            )
        img.paste(scaled, at, scaled)


class HdVariantLayout(HdLayout):
    """An HD layout whose four variants say whether Spotify and the energy chart show (see variants)."""

    SHOWS_SPOTIFY: ClassVar[bool] = True
    SHOWS_ENERGY: ClassVar[bool] = True

    def _draw_bottom_row(  # noqa: PLR0913, PLR0917 -- the frame, the panels, where, how tall, and the clock
        self,
        img: Image.Image,
        panels: dict[str, Panel],
        column: tuple[int, int],
        bottom: int,
        clock_format: ClockFormat,
        heights: tuple[int, int, int] = (120, 150, 150),
    ) -> int:
        """Draw the energy chart and Spotify's area (as this variant shows them) side by side, ending at `bottom`.

        `column` is the row's left edge and width, in screen pixels. `heights` are, in the panels' own: Spotify's
        beside the chart, Spotify's on its own (its wide now page, if _hd_panels swapped it in), and the chart's on
        its own. Returns the row's top (`bottom` if neither shows).
        """
        if not (self.SHOWS_SPOTIFY or self.SHOWS_ENERGY):
            return bottom
        x, width = column
        logical_width = width // SCALE
        beside, alone, energy_alone = heights
        area = self._spotify_area(panels, beside if self.SHOWS_ENERGY else alone) if self.SHOWS_SPOTIFY else []
        height = sum(panel_height for _, panel_height in area) + GAP * (len(area) - 1) if area else energy_alone
        top = bottom - SCALE * height

        spotify_x, spotify_width = x, logical_width
        if self.SHOWS_ENERGY:
            energy_width = round(logical_width * ENERGY_SHARE) if area else logical_width
            energy = panels[ClientClasses.GLOWMARKT.api_name]
            self._place_scaled(img, energy, (x, top), (energy_width, height), clock_format)
            spotify_x = x + SCALE * (energy_width + 3 * GAP)
            spotify_width = (x + width - spotify_x) // SCALE
        y = top
        for panel, panel_height in area:
            self._place_scaled(img, panel, (spotify_x, y), (spotify_width, panel_height), clock_format)
            y += SCALE * (panel_height + GAP)
        return top


def variants(base: type[HdVariantLayout]) -> list[Layout]:
    """Return `base`'s four variants, one per combination of Spotify and Glowmarkt, everything first.

    Each is a subclass named for what it shows (SpotifyGlowHdMastheadLayout, ..., BaseHdMastheadLayout), so the logs
    and the tests can tell them apart.
    """
    spotify, glow = ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT
    combinations = [
        ("SpotifyGlow", {spotify, glow}, set()),
        ("Spotify", {spotify}, {glow}),
        ("Glow", {glow}, {spotify}),
        ("Base", set(), {spotify, glow}),
    ]
    return [
        type(base)(
            f"{prefix}{base.__name__}",
            (base,),
            {
                "__module__": base.__module__,
                "__doc__": base.__doc__,
                "requires": frozenset(requires),
                "excludes": frozenset(excludes),
                "SHOWS_SPOTIFY": spotify not in excludes,
                "SHOWS_ENERGY": glow not in excludes,
            },
        )()
        for prefix, requires, excludes in combinations
    ]


def stops_of(panel: Panel) -> list[Panel]:
    """Return the stops on screen, one panel each, from the departures' panel: itself if it's a message."""
    return list(panel.arrival_panels) if isinstance(panel, CombinedArrivalPanel) else [panel]


def weather_parts(weather: Panel) -> tuple[tuple[Weather, ...], tuple[str, str] | None]:
    """Return the weather client's pages (now first), or no pages and what its message says.

    As the HD clock panels (weather.hd_clock_panels) take them.
    """
    if isinstance(weather, WeatherPanel):
        return weather.pages or (weather.weather,), None
    if isinstance(weather, MessagePanel):
        return (), (weather.title, weather.message)
    return (), None


def coming_hours(weather: Panel) -> tuple[WeatherHour, ...]:
    """Return the coming hours from the weather client's panel: none if it has no forecast."""
    if not isinstance(weather, WeatherPanel):
        return ()
    return next((page.hours for page in (weather.weather, *weather.pages) if page.hours), ())
