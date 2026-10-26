import countdown_renderer  # noqa: I001 -- see test_display_snapshots.py for why this comes first
import datetime as dt
from typing import TYPE_CHECKING

import pytest
from test_display_snapshots import (
    DEVELOPER_SUMMARY,
    EVERYTHING,
    FEW_NOTICES,
    NOTICES,
    SPOTIFY_QUEUE,
    _album_art_bytes,
    _forecast_weather,
    _many_route_arrivals,
    _panels,
    _spotify_page,
)

import countdown_renderer.glow.energy_panel
from countdown_renderer import moment
from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.departures.departures_client import DeparturesClient
from countdown_renderer.developer.developer_panel import DeveloperPanel
from countdown_renderer.glow.glow_client import GlowClient
from countdown_renderer.notices.column_notices_panel import TwoColumnNoticeBoardPanel
from countdown_renderer.notices.notice import Notice, Severity
from countdown_renderer.notices.notice_board_panel import NoticeBoardPanel
from countdown_renderer.rendering import place_developer_panel
from countdown_renderer.rendering.layouts.hd import SCALE, HdLayout
from countdown_renderer.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_renderer.rendering.layouts.hd_landscape_column import (
    EDGE,
    HD_LANDSCAPE_COLUMN_LAYOUTS,
    MARGIN,
    MIDDLE_X,
    RIGHT_X,
    WEATHER_WIDTH,
    WIDE_SCALE,
    HdLandscapeColumnLayout,
)
from countdown_renderer.rendering.layouts.hd_portrait_designs import STACKED_SCALE
from countdown_renderer.spotify.spotify_client import SpotifyClient
from countdown_renderer.spotify.spotify_panel import HdSpotifyPanel, SpotifyPanel
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_renderer.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_renderer.utils.clock import format_clock
from countdown_renderer.utils.scaled_draw import drawing_at
from countdown_renderer.weather import column_weather_panel
from countdown_renderer.weather.column_weather_panel import HOURS_KEPT, ColumnWeatherPanel
from countdown_renderer.weather.weather_client import WeatherClient

if TYPE_CHECKING:
    from countdown_renderer.panel import Panel
    from countdown_renderer.rendering.layout import Layout

SPOTIFY, GLOW, DEVELOPER = ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT, ClientClasses.DEVELOPER
VARIANTS = [
    (set(), "SpotifyGlow"),
    ({GLOW}, "Spotify"),
    ({SPOTIFY}, "Glow"),
    ({SPOTIFY, GLOW}, "Base"),
]
# Naive, as the energy panel's clock is; the clock panels draw the render's moment, aware.
_FROZEN_NOW = dt.datetime(2026, 1, 15, 12, 0, 0)  # noqa: DTZ001 -- see above


class _FrozenDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    monkeypatch.setattr(countdown_renderer.glow.energy_panel, "datetime", _FrozenDatetime)
    with moment.drawing_at(_FROZEN_NOW.replace(tzinfo=dt.UTC)):
        yield


def _variant(prefix: str) -> Layout:
    [layout] = [layout for layout in HD_LANDSCAPE_COLUMN_LAYOUTS if type(layout).__name__.startswith(f"{prefix}Hd")]
    return layout


def _full_panels(notices: Panel = NOTICES, *, developer: bool = False) -> dict[str, Panel]:
    """Everything to show, with five notices (two columns of them), and the developer panel if asked."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())
    panels = _panels(_many_route_arrivals(), weather=_forecast_weather(), spotify=spotify, notices=notices)
    if developer:
        panels[DEVELOPER.api_name] = DeveloperPanel(DEVELOPER_SUMMARY)
    return panels


def _render(layout: Layout, panels: dict[str, Panel], available: frozenset[ClientClasses] = EVERYTHING):
    """Draw `panels` as a render hands them over (the developer panel placed), checking the frame's size."""
    image = layout.render(place_developer_panel(panels, available), HD_LANDSCAPE).image
    assert image.size == (1872, 1404)
    return image


# --- Goldens ---------------------------------------------------------------------------------------------------------


def test_column_layout_on_an_hd_landscape_screen(snapshot):
    """The weather down the left, the stops in the middle, Spotify, the developer strip and the energy chart right."""
    img = _render(_variant("SpotifyGlow"), _full_panels(developer=True), EVERYTHING | {DEVELOPER})
    snapshot.assert_matches("screen_hd_landscape_layout_column", img)


def test_column_layout_without_the_energy_chart(snapshot):
    """No right column: the stops side by side across its width too, Spotify's wide page in a row under them."""
    img = _render(_variant("Spotify"), _full_panels(FEW_NOTICES), EVERYTHING - {GLOW})
    snapshot.assert_matches("screen_hd_landscape_layout_column_no_energy", img)


# --- Variants --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_each_variant_fits_exactly_what_it_shows(unavailable, prefix):
    layout = _variant(prefix)
    available = EVERYTHING - unavailable

    assert isinstance(layout, HdLandscapeColumnLayout)
    assert layout.layout_id == "column"
    assert layout.canvases == frozenset({HD_LANDSCAPE})
    assert [type(other).__name__ for other in HD_LANDSCAPE_COLUMN_LAYOUTS if other.fits(available)] == [
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
        ClientClasses.NOTICE_BOARD.api_name: NoticeBoardPanel([]),
    }


@pytest.mark.parametrize("client", [*_message_panels(), "all"])
@pytest.mark.parametrize("layout", HD_LANDSCAPE_COLUMN_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_a_message_in_place_of_a_client_still_draws_the_whole_frame(layout, client):
    messages = _message_panels()
    panels = messages if client == "all" else _full_panels() | {client: messages[client]}

    _render(layout, panels)


@pytest.mark.parametrize("clock_format", ["12h", "24h"])
@pytest.mark.parametrize("layout", HD_LANDSCAPE_COLUMN_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_every_variant_draws_with_the_developer_panel_and_spotifys_top_lists(layout, clock_format):
    """Spotify's top lists in its place, and the developer panel in Spotify's place, in either clock."""
    available = EVERYTHING | {DEVELOPER}
    top = _full_panels(developer=True) | {SPOTIFY.api_name: _spotify_page("top", "hero")}
    assert layout.render(place_developer_panel(top, available), HD_LANDSCAPE, clock_format=clock_format)
    alone = place_developer_panel(_full_panels(developer=True), available - {SPOTIFY})
    assert layout.render(alone, HD_LANDSCAPE, clock_format=clock_format)


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
def test_spotify_and_the_energy_chart_are_placed_only_in_the_variants_that_show_them(monkeypatch, unavailable, prefix):
    placed = _placed(monkeypatch)
    panels = _full_panels()

    _render(_variant(prefix), panels, EVERYTHING - unavailable)

    drawn = [panel for panel, *_ in placed]
    assert any(panel is panels[GLOW.api_name] for panel in drawn) is (GLOW not in unavailable)
    spotifys = [panel for panel in drawn if isinstance(panel, SpotifyPanel)]
    assert bool(spotifys) is (SPOTIFY not in unavailable)
    # Wide only in the row under the stops, with no right column.
    assert all(isinstance(panel, HdSpotifyPanel) is (GLOW in unavailable) for panel in spotifys)


# --- The arrangement -------------------------------------------------------------------------------------------------


def _stops(placed: list[Placed]) -> list[Placed]:
    return [entry for entry in placed if isinstance(entry[0], AbstractArrivalPanel | CombinedArrivalPanel)]


def test_with_the_energy_chart_the_stops_are_one_under_the_other_between_the_weather_and_the_right_column(
    monkeypatch,
):
    placed = _placed(monkeypatch)
    panels = _full_panels(developer=True)

    _render(_variant("SpotifyGlow"), panels, EVERYTHING | {DEVELOPER})

    [(_weather, at, size, _scale)] = [entry for entry in placed if isinstance(entry[0], ColumnWeatherPanel)]
    assert (at, size[0]) == ((MARGIN, EDGE), WEATHER_WIDTH)
    (first, first_at, first_size, scale), (second, second_at, _size, _scale) = _stops(placed)
    assert [first, second] == list(panels[ClientClasses.TFL.api_name].arrival_panels)
    assert scale == STACKED_SCALE
    assert first_at[0] == second_at[0] == MIDDLE_X
    assert first_at[0] + first_size[0] * scale <= RIGHT_X
    assert second_at[1] > first_at[1] + first_size[1] * scale
    # Down the right: the song, the developer panel's strip under it, then the chart.
    right = [panel for panel, at, *_ in placed if at[0] == RIGHT_X]
    assert [type(panel) for panel in right] == [SpotifyPanel, DeveloperPanel, type(panels[GLOW.api_name])]
    assert right[1].compact


@pytest.mark.parametrize("prefix", ["Spotify", "Base"])
def test_without_the_energy_chart_the_stops_are_side_by_side_across_the_right_column_too(monkeypatch, prefix):
    placed = _placed(monkeypatch)
    panels = _full_panels()

    _render(_variant(prefix), panels, EVERYTHING - {GLOW, *([SPOTIFY] if prefix == "Base" else [])})

    [(stops, at, size, scale)] = _stops(placed)
    assert stops is panels[ClientClasses.TFL.api_name]
    assert (at, scale) == ((MIDDLE_X, EDGE), WIDE_SCALE)
    assert MIDDLE_X + size[0] * scale == pytest.approx(HD_LANDSCAPE.width - MARGIN, abs=WIDE_SCALE)
    assert not any(at[0] >= RIGHT_X for _panel, at, *_ in placed)


def test_the_stops_get_the_room_spotify_leaves(monkeypatch):
    placed = _placed(monkeypatch)
    rooms = {}
    for prefix, available in (("Spotify", EVERYTHING - {GLOW}), ("Base", EVERYTHING - {GLOW, SPOTIFY})):
        placed.clear()
        _render(_variant(prefix), _full_panels(), available)
        [(_stops_panel, _at, size, _scale)] = _stops(placed)
        rooms[prefix] = size[1]

    assert rooms["Base"] > rooms["Spotify"]


def test_the_developer_panel_in_spotifys_place_is_its_strip_in_the_right_column(monkeypatch):
    placed = _placed(monkeypatch)

    _render(_variant("SpotifyGlow"), _full_panels(developer=True), EVERYTHING - {SPOTIFY} | {DEVELOPER})

    [developer] = [panel for panel, *_ in placed if isinstance(panel, DeveloperPanel)]
    assert developer.compact
    assert not any(isinstance(panel, SpotifyPanel) for panel, *_ in placed)


def test_the_notices_run_along_the_foot_in_two_columns(monkeypatch):
    placed = _placed(monkeypatch)

    _render(_variant("SpotifyGlow"), _full_panels())

    [(board, at, size, _scale)] = [entry for entry in placed if isinstance(entry[0], NoticeBoardPanel)]
    assert isinstance(board, TwoColumnNoticeBoardPanel)
    assert board.notices == NOTICES.notices
    assert at[0] == MARGIN
    assert size[0] == (HD_LANDSCAPE.width - 2 * MARGIN) // SCALE
    assert at[1] + SCALE * size[1] == HD_LANDSCAPE.height - EDGE
    above = [(entry_at, entry_size, scale) for panel, entry_at, entry_size, scale in placed if panel is not board]
    assert all(entry_at[1] + entry_size[1] * scale <= at[1] for entry_at, entry_size, scale in above)


def test_without_notices_the_columns_run_to_the_foot(monkeypatch):
    placed = _placed(monkeypatch)

    _render(_variant("SpotifyGlow"), _full_panels(NoticeBoardPanel([])))

    assert not any(isinstance(panel, NoticeBoardPanel) for panel, *_ in placed)
    [(_weather, at, size, _scale)] = [entry for entry in placed if isinstance(entry[0], ColumnWeatherPanel)]
    assert at[1] + SCALE * size[1] == HD_LANDSCAPE.height - EDGE


# --- The two-column notice board -------------------------------------------------------------------------------------


def _notices(count: int, severity: Severity = Severity.WARNING) -> list[Notice]:
    return [Notice(f"Line {i}", severity, f"Notice number {i}") for i in range(count)]


@pytest.mark.parametrize(("count", "rows"), [(1, 1), (2, 1), (3, 2), (5, 3)])
def test_the_two_column_board_needs_a_row_per_two_notices(count, rows):
    board = TwoColumnNoticeBoardPanel(_notices(count))
    single = NoticeBoardPanel(_notices(count))

    assert board.fitted_height(500) == 32 + rows * board.row_height + 2  # the hero style's chrome
    assert board.fitted_height(40) == 40
    assert TwoColumnNoticeBoardPanel([]).fitted_height(500) == 0
    assert single.fitted_height(500) - 32 - 2 == count * single.row_height


def test_the_two_column_board_counts_what_doesnt_fit_in_its_last_place(monkeypatch):
    drawn: list[str] = []
    board = TwoColumnNoticeBoardPanel(_notices(7))
    monkeypatch.setattr(board, "_draw_row", lambda _d, notice, *_args: drawn.append(notice.source))
    texts: list[str] = []
    monkeypatch.setattr(
        "countdown_renderer.utils.scaled_draw.ScaledDraw.text",
        lambda _self, _xy, text, *_args, **_kw: texts.append(text),
    )

    with drawing_at(1):
        board.render(600, 32 + 2 * board.row_height + 2)

    assert drawn == ["Line 0", "Line 1", "Line 2"]
    assert "+4 more" in texts


def test_planned_works_only_fill_the_places_left_over():
    planned = Notice("Central", Severity.PLANNED, "Closed at the weekend")
    board = TwoColumnNoticeBoardPanel([planned, *_notices(4)])
    drawn: list[str] = []
    board._draw_row = lambda _d, notice, *_args: drawn.append(notice.source)

    board.render(600, 32 + 2 * board.row_height + 2)

    assert drawn == ["Line 0", "Line 1", "Line 2", "Line 3"]


# --- The weather column ----------------------------------------------------------------------------------------------


def _hours_and_pages(height: int) -> tuple[int, int]:
    """Draw the weather column `height` tall; return how many hours and later pages it drew."""
    weather = _forecast_weather()
    panel = ColumnWeatherPanel(weather.pages)
    hours = [format_clock(hour.time, "24h") for hour in weather.pages[0].hours]
    pages = ["TONIGHT", "TOMORROW", "WEEKEND"]
    texts: list[str] = []
    original = column_weather_panel._fitted

    def record(d, xy, text, *args):
        texts.append(text)
        original(d, xy, text, *args)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(column_weather_panel, "_fitted", record)
        panel.render(WEATHER_WIDTH, height)
    return (
        sum(text in hours for text in texts),
        sum(any(text.startswith(page) for page in pages) for text in texts),
    )


def test_the_weather_column_keeps_four_hours_before_the_later_pages():
    assert _hours_and_pages(540) == (HOURS_KEPT, 0)
    assert _hours_and_pages(590) == (HOURS_KEPT, 1)
    tall_hours, tall_pages = _hours_and_pages(700)
    assert tall_pages == 3
    assert tall_hours >= HOURS_KEPT


def test_the_weather_column_says_why_there_is_no_weather():
    panel = ColumnWeatherPanel((), ("Weather", "Could not connect"))

    assert panel.render(WEATHER_WIDTH, 540).size == (WEATHER_WIDTH, 540)
