import countdown_core  # noqa: I001 -- see test_display_snapshots.py for why this comes first
import datetime as dt
from typing import TYPE_CHECKING

import pytest
from tfl_factory import tfl_panel
from weather_factory import forecast_json
from test_display_snapshots import (
    _FROZEN_NOW,
    BUS_STOP,
    DEVELOPER_SUMMARY,
    ENERGY,
    EVERYTHING,
    NOTICES,
    SPOTIFY_QUEUE,
    _album_art_bytes,
    _many_route_arrivals,
    _panels,
)

import countdown_core.glow.energy_panel
from countdown_core.core import moment
from countdown_core.core.api_registry import ClientClasses
from countdown_core.departures.departures_client import DeparturesClient
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.glow.glow_client import GlowClient
from countdown_core.notices.focus_notices_panel import FocusNoticesPanel
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering.layouts.hd import SCALE, HdLayout
from countdown_core.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_core.rendering.layouts.hd_landscape_focus import (
    FOCUS_SCALE,
    HD_LANDSCAPE_FOCUS_LAYOUTS,
    HdLandscapeFocusLayout,
)
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel, Rotation
from countdown_core.tfl.focus_panel import FocusPanel
from countdown_core.tfl.models import BusArrival
from countdown_core.weather.focus_weather_panel import FocusWeatherPanel
from countdown_core.weather.models import ForecastResponse
from countdown_core.weather.weather_client import WeatherClient
from countdown_core.weather.weather_forecast import weather_pages
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.rendering.layout import Layout

SPOTIFY, GLOW = ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT
VARIANTS = [
    (set(), "SpotifyGlow"),
    ({GLOW}, "Spotify"),
    ({SPOTIFY}, "Glow"),
    ({SPOTIFY, GLOW}, "Base"),
]


class _FrozenDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    monkeypatch.setattr(countdown_core.glow.energy_panel, "datetime", _FrozenDatetime)
    with moment.drawing_at(_FROZEN_NOW.replace(tzinfo=dt.UTC)):
        yield


def _forecast_weather() -> WeatherPanel:
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    return WeatherPanel(pages[0], "columns", pages)


def _full_panels(**replaced: Panel) -> dict[str, Panel]:
    """Everything, as a render hands the layout: Spotify playing with the developer strip, and five notices."""
    panels = _panels(
        _many_route_arrivals(),
        weather=_forecast_weather(),
        spotify=SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes()),
        notices=NOTICES,
    )
    panels[ClientClasses.DEVELOPER.api_name] = DeveloperPanel(DEVELOPER_SUMMARY).compacted()
    return panels | {getattr(ClientClasses, name.upper()).api_name: panel for name, panel in replaced.items()}


def _variant(prefix: str) -> Layout:
    [layout] = [layout for layout in HD_LANDSCAPE_FOCUS_LAYOUTS if type(layout).__name__.startswith(f"{prefix}Hd")]
    return layout


def _draw(panels: dict[str, Panel], prefix: str = "SpotifyGlow"):
    return _variant(prefix).render(panels, HD_LANDSCAPE).image


# --- Goldens -------------------------------------------------------------------------------------------------------


def test_the_focus_layout_with_everything(snapshot):
    """Each stop's next departure huge, the rest in lists; the time, weather, energy chart, Spotify and the strip."""
    snapshot.assert_matches("screen_hd_landscape_layout_focus", _draw(_full_panels()))


def test_the_focus_layout_without_spotify_or_the_energy_chart(snapshot):
    """Their room goes to the weather, which lists the forecast's later pages too."""
    snapshot.assert_matches("screen_hd_landscape_layout_focus_base", _draw(_full_panels(), "Base"))


def test_the_focus_layout_with_one_stop_due_and_a_weather_message(snapshot):
    """One stop's routes run on into the second list; the rotation's squares over them; due instead of minutes."""
    buses = [
        BusArrival(naptanId="490000123W", lineName=line, timeToStation=minutes * 60, modeName="bus", destinationName=to)
        for line, to, minutes in [
            ("36", "Queen's Park", 0),
            ("185", "Victoria", 4),
            ("436", "Paddington", 11),
            ("36", "Queen's Park", 12),
            ("12", "Oxford Circus", 19),
            ("148", "White City", 21),
            ("171", "Holborn", 23),
        ]
    ]
    one_stop = CombinedArrivalPanel([tfl_panel(BUS_STOP, buses, "hero")], Rotation(1, 3))
    panels = _full_panels(tfl=one_stop, weather=WeatherClient.message_panel("Could not connect"))
    snapshot.assert_matches("screen_hd_landscape_layout_focus_one_stop", _draw(panels))


# --- Variants ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_each_variant_fits_the_apis_it_shows(unavailable, prefix):
    layout = _variant(prefix)
    available = EVERYTHING - unavailable

    assert isinstance(layout, HdLandscapeFocusLayout)
    assert layout.layout_id == "focus"
    assert layout.canvases == frozenset({HD_LANDSCAPE})
    assert [type(other).__name__ for other in HD_LANDSCAPE_FOCUS_LAYOUTS if other.fits(available)] == [
        type(layout).__name__
    ]
    assert layout.SHOWS_SPOTIFY is (SPOTIFY not in unavailable)
    assert layout.SHOWS_ENERGY is (GLOW not in unavailable)


def _message_panels() -> dict[str, Panel]:
    return {
        ClientClasses.TFL.api_name: DeparturesClient.message_panel("Needs configuring"),
        GLOW.api_name: GlowClient.message_panel("Not configured"),
        ClientClasses.WEATHER.api_name: WeatherClient.message_panel("Could not connect"),
        SPOTIFY.api_name: SpotifyClient.message_panel("Not configured"),
        ClientClasses.NOTICE_BOARD.api_name: MessagePanel("Notices", "Could not connect"),
    }


@pytest.mark.parametrize(
    "client", [ClientClasses.TFL, GLOW, ClientClasses.WEATHER, SPOTIFY, ClientClasses.NOTICE_BOARD, None]
)
@pytest.mark.parametrize("layout", HD_LANDSCAPE_FOCUS_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_a_message_in_place_of_a_client_still_draws_a_whole_frame(layout, client):
    panels = _full_panels()
    if client is None:
        panels = _message_panels()
    else:
        panels[client.api_name] = _message_panels()[client.api_name]

    assert layout.render(panels, HD_LANDSCAPE).image.size == (1872, 1404)
    assert layout.render(panels, HD_LANDSCAPE, clock_format="12h").image.size == (1872, 1404)


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


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_what_a_variant_does_not_show_is_not_placed(monkeypatch, unavailable, prefix):
    placed = _placed(monkeypatch)

    _draw(_full_panels(), prefix)

    drawn = [panel for panel, *_ in placed]
    assert any(panel is ENERGY for panel in drawn) is (GLOW not in unavailable)
    assert any(isinstance(panel, SpotifyPanel) for panel in drawn) is (SPOTIFY not in unavailable)
    assert any(isinstance(panel, DeveloperPanel) for panel in drawn) is (SPOTIFY not in unavailable)


def test_the_weather_gets_the_room_spotify_and_the_energy_chart_leave(monkeypatch):
    placed = _placed(monkeypatch)
    heights = {}
    for _unavailable, prefix in VARIANTS:
        placed.clear()
        _draw(_full_panels(), prefix)
        [heights[prefix]] = [size[1] for panel, _at, size, _scale in placed if isinstance(panel, FocusWeatherPanel)]

    assert heights["SpotifyGlow"] < heights["Spotify"] <= heights["Base"]
    assert heights["SpotifyGlow"] < heights["Glow"] <= heights["Base"]


def test_the_stops_are_drawn_about_as_big_as_the_sidebar_layout_draws_them(monkeypatch):
    placed = _placed(monkeypatch)

    _draw(_full_panels())

    [(panel, at, _size, scale)] = [placed_one for placed_one in placed if isinstance(placed_one[0], FocusPanel)]
    assert scale == FOCUS_SCALE
    assert at[0] == 48
    assert [stop.stop_name for stop in panel.stops] == ["Elephant & Castle", "Kennington"]


def test_the_notices_run_along_the_foot(monkeypatch):
    placed = _placed(monkeypatch)

    _draw(_full_panels())

    [(panel, at, size, scale)] = [one for one in placed if isinstance(one[0], FocusNoticesPanel)]
    assert panel.notices == tuple(NOTICES.notices)
    assert at[1] + size[1] * scale == 1404 - 30


def test_no_notices_leave_the_foot_to_the_rest(monkeypatch):
    placed = _placed(monkeypatch)

    _draw(_full_panels(notice_board=NoticeBoardPanel([])))

    assert not any(isinstance(panel, FocusNoticesPanel) for panel, *_ in placed)


# --- The panels ----------------------------------------------------------------------------------------------------


def test_the_notices_count_what_does_not_fit_but_planned_works():
    long = "A notice long enough that only one of them fits on a line of the notices along the foot " * 2
    notices = [Notice("TfL", Severity.WARNING, long) for _ in range(4)]
    planned = Notice("Central", Severity.PLANNED, long)

    panel = FocusNoticesPanel([*notices, planned])

    assert panel.fitted_height(500) == 52
    assert panel.render(888, 52).size == (888, 52)


def test_a_stop_with_nothing_leaving_still_has_its_name():
    stop = tfl_panel(BUS_STOP, [], "hero")

    image = FocusPanel([stop]).render(400, 300)

    assert image.getbbox() is not None


def test_the_weather_without_a_forecast_is_only_as_tall_as_its_message():
    assert FocusWeatherPanel((), ("Weather", "Could not connect")).fitted_height(500) < 500
    assert FocusWeatherPanel().fitted_height(500) < FocusWeatherPanel((), ("Weather", "x")).fitted_height(500)
