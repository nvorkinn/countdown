import countdown_core  # noqa: I001 -- see test_display_snapshots.py for why this comes first
import datetime as dt
from typing import TYPE_CHECKING

import pytest
from test_display_snapshots import (
    _FROZEN_NOW,
    DEVELOPER_SUMMARY,
    EVERYTHING,
    NOTICES,
    SPOTIFY_QUEUE,
    WEATHER,
    _album_art_bytes,
    _forecast_weather,
    _many_route_arrivals,
    _panels,
)

import countdown_core.glow.energy_panel
from countdown_core.core import moment
from countdown_core.core.api_registry import ClientClasses
from countdown_core.departures.departures_client import DeparturesClient
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.glow.glow_client import GlowClient
from countdown_core.notices.notice_board_panel import HdNoticeBoardPanel, NoticeBoardPanel
from countdown_core.notices.notice_board_client import NoticeBoardClient
from countdown_core.rendering import place_developer_panel
from countdown_core.rendering.layouts import hd_landscape_pages
from countdown_core.rendering.layouts.hd import SCALE, HdLayout
from countdown_core.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_core.rendering.layouts.hd_landscape_pages import (
    HD_LANDSCAPE_PAGES_LAYOUTS,
    MARGIN,
    STOP_SCALE,
    HdLandscapePagesLayout,
)
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import HdSpotifyPanel, SpotifyPanel
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_core.weather.hd_clock_panels import HdClockPanel
from countdown_core.weather.pages_weather_panel import HdPagesWeatherPanel
from countdown_core.weather.weather_client import WeatherClient

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.rendering.layout import Layout

SPOTIFY, GLOW, DEVELOPER = ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT, ClientClasses.DEVELOPER
VARIANTS = [
    (set(), "SpotifyGlow"),
    ({GLOW}, "Spotify"),
    ({SPOTIFY}, "Glow"),
    ({SPOTIFY, GLOW}, "Base"),
]
OUT, HOME = 0, 1


class _FrozenDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    monkeypatch.setattr(countdown_core.glow.energy_panel, "datetime", _FrozenDatetime)
    with moment.drawing_at(_FROZEN_NOW.replace(tzinfo=dt.UTC)):
        yield


def _full_panels(weather: Panel | None = None) -> dict[str, Panel]:
    """Everything to show, with the developer panel's strip under Spotify's song."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())
    weather = weather if weather is not None else _forecast_weather()
    panels = _panels(_many_route_arrivals(), weather=weather, spotify=spotify, notices=NOTICES)
    panels[DEVELOPER.api_name] = DeveloperPanel(DEVELOPER_SUMMARY)
    return place_developer_panel(panels, EVERYTHING | {DEVELOPER})


def _message_panels() -> dict[str, Panel]:
    """Return a message in place of every client's panel."""
    return {
        ClientClasses.TFL.api_name: DeparturesClient.message_panel("Needs configuring"),
        GLOW.api_name: GlowClient.message_panel("Not configured"),
        ClientClasses.WEATHER.api_name: WeatherClient.message_panel("Could not connect"),
        SPOTIFY.api_name: SpotifyClient.message_panel("Not configured"),
        ClientClasses.NOTICE_BOARD.api_name: NoticeBoardClient.message_panel("Could not connect"),
    }


def _variant(prefix: str) -> Layout:
    [layout] = [layout for layout in HD_LANDSCAPE_PAGES_LAYOUTS if type(layout).__name__.startswith(f"{prefix}Hd")]
    return layout


Placed = tuple["Panel", tuple[int, int], tuple[int, int], float]


def _placed(monkeypatch) -> list[Placed]:
    """Record every panel the layout places: where (in screen pixels), how big (in its own) and at which scale."""
    placed: list[Placed] = []
    place = HdLayout._place_scaled

    def record(img, panel, at, size, clock_format, scale=SCALE):  # noqa: PLR0913, PLR0917 -- _place_scaled's signature
        placed.append((panel, at, size, scale))
        place(img, panel, at, size, clock_format, scale)

    monkeypatch.setattr(HdLayout, "_place_scaled", staticmethod(record))
    return placed


# --- What it looks like ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("page", "name"), [(OUT, "out"), (HOME, "home")])
def test_each_page_of_the_pages_layout_on_an_hd_landscape_screen(snapshot, rotation_clock, page, name):
    """Going out: the stops side by side and the notices. At home: the weather big, energy, Spotify, the developer."""
    rotation_clock.at_minute(page)

    img = _variant("SpotifyGlow").render(_full_panels(), HD_LANDSCAPE).image

    assert img.size == (1872, 1404)
    snapshot.assert_matches(f"screen_hd_landscape_layout_pages_{name}", img)


# --- The variants ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_each_variant_fits_the_apis_it_shows(unavailable, prefix):
    available = frozenset(EVERYTHING - unavailable)

    [fitting] = [layout for layout in HD_LANDSCAPE_PAGES_LAYOUTS if layout.fits(available)]

    assert type(fitting).__name__ == f"{prefix}HdLandscapePagesLayout"
    assert isinstance(fitting, HdLandscapePagesLayout)
    assert fitting.layout_id == "pages"
    assert fitting.canvases == frozenset({HD_LANDSCAPE})
    assert fitting.SHOWS_SPOTIFY is (SPOTIFY not in unavailable)
    assert fitting.SHOWS_ENERGY is (GLOW not in unavailable)


@pytest.mark.parametrize("page", [OUT, HOME], ids=["out", "home"])
@pytest.mark.parametrize("panels", [_full_panels, _message_panels], ids=["full", "messages"])
@pytest.mark.parametrize("layout", HD_LANDSCAPE_PAGES_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_every_variant_draws_an_hd_landscape_frame(layout, panels, page, rotation_clock):
    rotation_clock.at_minute(page)

    assert layout.render(panels(), HD_LANDSCAPE).image.size == HD_LANDSCAPE.size


@pytest.mark.parametrize(
    "name",
    [
        ClientClasses.TFL.api_name,
        GLOW.api_name,
        ClientClasses.WEATHER.api_name,
        SPOTIFY.api_name,
        ClientClasses.NOTICE_BOARD.api_name,
    ],
)
@pytest.mark.parametrize("page", [OUT, HOME], ids=["out", "home"])
@pytest.mark.parametrize("layout", HD_LANDSCAPE_PAGES_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_a_message_in_place_of_any_client_still_draws_a_frame(layout, page, name, rotation_clock):
    rotation_clock.at_minute(page)
    panels = _full_panels() | {name: _message_panels()[name]}

    assert layout.render(panels, HD_LANDSCAPE, clock_format="12h").image.size == HD_LANDSCAPE.size


@pytest.mark.parametrize("layout", HD_LANDSCAPE_PAGES_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_a_lone_weather_page_without_hours_still_draws_a_frame(layout, rotation_clock):
    rotation_clock.at_minute(HOME)

    assert layout.render(_full_panels(WEATHER), HD_LANDSCAPE).image.size == HD_LANDSCAPE.size


@pytest.mark.parametrize("page", [OUT, HOME], ids=["out", "home"])
@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_spotify_and_the_energy_chart_are_placed_only_at_home_in_the_variants_that_show_them(
    monkeypatch, rotation_clock, unavailable, prefix, page
):
    rotation_clock.at_minute(page)
    placed = _placed(monkeypatch)
    panels = _full_panels()

    _variant(prefix).render(panels, HD_LANDSCAPE)

    drawn = [panel for panel, *_ in placed]
    at_home = page == HOME
    assert any(panel is panels[GLOW.api_name] for panel in drawn) is (GLOW not in unavailable and at_home)
    spotifys = [panel for panel in drawn if isinstance(panel, SpotifyPanel)]
    assert bool(spotifys) is (SPOTIFY not in unavailable and at_home)
    developer = any(isinstance(panel, DeveloperPanel) for panel in drawn)
    assert developer is (SPOTIFY not in unavailable and at_home)
    # Spotify's wide now page only with the width to itself.
    assert all(isinstance(panel, HdSpotifyPanel) is (GLOW in unavailable) for panel in spotifys)


# --- The pages -------------------------------------------------------------------------------------------------------


def test_going_out_has_the_stops_side_by_side_and_the_notices_along_the_foot(monkeypatch, rotation_clock):
    rotation_clock.at_minute(OUT)
    placed = _placed(monkeypatch)
    panels = _full_panels()

    _variant("SpotifyGlow").render(panels, HD_LANDSCAPE)

    [header] = [panel for panel, *_ in placed if isinstance(panel, HdClockPanel)]
    assert (header.squares, header.summary) == ((0, 2), None)
    assert header.pages
    stops = [(panel, at, scale) for panel, at, _size, scale in placed if isinstance(panel, AbstractArrivalPanel)]
    assert [panel for panel, *_ in stops] == list(panels[ClientClasses.TFL.api_name].arrival_panels)
    (_first, first_at, first_scale), (_second, second_at, second_scale) = stops
    assert first_at[1] == second_at[1]
    assert first_at[0] == MARGIN < second_at[0]
    assert first_scale == second_scale == STOP_SCALE
    [(_notices, at, size, _scale)] = [entry for entry in placed if isinstance(entry[0], HdNoticeBoardPanel)]
    assert at[1] + SCALE * size[1] == HD_LANDSCAPE.height - hd_landscape_pages.EDGE
    assert not any(isinstance(panel, HdPagesWeatherPanel) for panel, *_ in placed)


def test_at_home_the_header_has_the_next_departures_and_the_weather_is_big(monkeypatch, rotation_clock):
    rotation_clock.at_minute(HOME)
    placed = _placed(monkeypatch)
    weather = _forecast_weather()

    _variant("SpotifyGlow").render(_full_panels(weather), HD_LANDSCAPE)

    [header] = [panel for panel, *_ in placed if isinstance(panel, HdClockPanel)]
    assert header.squares == (1, 2)
    assert header.summary == "36 in 2 min   N in 1 min"
    [(big, at, size, scale)] = [entry for entry in placed if isinstance(entry[0], HdPagesWeatherPanel)]
    assert big.pages == tuple(weather.pages)
    assert (at[0], round(size[0] * scale)) == (MARGIN, hd_landscape_pages.WEATHER_WIDTH)
    assert not any(isinstance(panel, AbstractArrivalPanel | NoticeBoardPanel) for panel, *_ in placed)


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS[1:])
def test_without_the_energy_chart_the_weather_takes_the_width(monkeypatch, rotation_clock, unavailable, prefix):
    """Without Spotify, the energy chart takes the right column instead."""
    rotation_clock.at_minute(HOME)
    placed = _placed(monkeypatch)

    _variant(prefix).render(_full_panels(), HD_LANDSCAPE)

    [(_big, _at, size, scale)] = [entry for entry in placed if isinstance(entry[0], HdPagesWeatherPanel)]
    if GLOW in unavailable:
        assert round(size[0] * scale) == pytest.approx(HD_LANDSCAPE.width - 2 * MARGIN, abs=3)
    else:
        assert round(size[0] * scale) == hd_landscape_pages.WEATHER_WIDTH


def test_at_home_the_weathers_message_is_drawn_in_its_place(monkeypatch, rotation_clock):
    rotation_clock.at_minute(HOME)
    placed = _placed(monkeypatch)
    message = WeatherClient.message_panel("Could not connect")

    _variant("Base").render(_full_panels(message), HD_LANDSCAPE)

    [(_panel, at, _size, scale)] = [entry for entry in placed if entry[0] is message]
    assert (at[0], scale) == (MARGIN, SCALE)
    assert not any(isinstance(panel, HdPagesWeatherPanel) for panel, *_ in placed)


def test_with_nothing_leaving_the_home_header_has_the_weather_now(monkeypatch, rotation_clock):
    rotation_clock.at_minute(HOME)
    placed = _placed(monkeypatch)
    panels = _full_panels() | {ClientClasses.TFL.api_name: DeparturesClient.message_panel("Needs configuring")}

    _variant("SpotifyGlow").render(panels, HD_LANDSCAPE)

    [header] = [panel for panel, *_ in placed if isinstance(panel, HdClockPanel)]
    assert header.summary is None
    assert header.pages
