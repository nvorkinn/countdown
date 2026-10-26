import countdown_renderer  # noqa: I001 -- see test_display_snapshots.py for why this comes first
import datetime as dt

import pytest
from test_display_snapshots import (
    _FROZEN_NOW,
    BUS_STOP,
    DEVELOPER_AND_SPOTIFY,
    DEVELOPER_SUMMARY,
    EVERYTHING,
    FEW_NOTICES,
    NO_GLOWMARKT,
    SPOTIFY_QUEUE,
    TUBE_STOP,
    _album_art_bytes,
    ENERGY,
    _cover_art_bytes,
    _forecast_weather,
    _many_route_arrivals,
    _panels,
)
from tfl_factory import tfl_panel

import countdown_renderer.glow.energy_panel
from countdown_renderer import moment
from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.departures.departures_client import DeparturesClient
from countdown_renderer.developer.developer_panel import DeveloperPanel
from countdown_renderer.glow.glow_client import GlowClient
from countdown_renderer.notices.notice_board_panel import NoticeBannerPanel, NoticeBoardPanel
from countdown_renderer.notices.notice_board_client import NoticeBoardClient
from countdown_renderer.rendering import place_developer_panel
from countdown_renderer.rendering.layouts.hd import SCALE, HdLayout
from countdown_renderer.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_renderer.rendering.layouts.hd_landscape_board import (
    BOARD_SCALE,
    HD_LANDSCAPE_BOARD_LAYOUTS,
    STRIP_HEIGHT,
    HdLandscapeBoardLayout,
)
from countdown_renderer.spotify.spotify_client import SpotifyClient
from countdown_renderer.spotify.spotify_panel import HdSpotifyPanel, SpotifyPanel
from countdown_renderer.tfl.board_stop_panel import BAR, ROW, BoardStopPanel
from countdown_renderer.tfl.combined_arrival_panel import CombinedArrivalPanel, Rotation
from countdown_core.tfl.models import BusArrival
from countdown_renderer.utils.utils import GAP
from countdown_renderer.weather.board_weather_panels import BoardHeaderPanel, BoardWeatherPanel
from countdown_renderer.weather.hd_sidebar_panel import HdHoursPanel
from countdown_renderer.weather.weather_client import WeatherClient

SPOTIFY, GLOW = ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT
# What each variant leaves out, by the prefix variants() names it with.
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
    """Freeze the energy chart's clock, and the render's moment the header's clock shows."""
    monkeypatch.setattr(countdown_renderer.glow.energy_panel, "datetime", _FrozenDatetime)
    with moment.drawing_at(_FROZEN_NOW.replace(tzinfo=dt.UTC)):
        yield


def _variant(prefix: str) -> HdLandscapeBoardLayout:
    [layout] = [layout for layout in HD_LANDSCAPE_BOARD_LAYOUTS if type(layout).__name__.startswith(f"{prefix}Hd")]
    assert isinstance(layout, HdLandscapeBoardLayout)
    return layout


def _full_panels():
    """Return every client with something to show: a forecast, two busy stops, Spotify playing and two notices."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())
    return _panels(_many_route_arrivals(), weather=_forecast_weather(), spotify=spotify, notices=FEW_NOTICES)


def _messages():
    """Return a message in place of every client's panel, and no notices."""
    return {
        ClientClasses.TFL.api_name: DeparturesClient.message_panel("Needs configuring"),
        GLOW.api_name: GlowClient.message_panel("Not configured"),
        ClientClasses.WEATHER.api_name: WeatherClient.message_panel("Could not connect"),
        SPOTIFY.api_name: SpotifyClient.message_panel("Not configured"),
        ClientClasses.NOTICE_BOARD.api_name: NoticeBoardPanel([]),
    }


Placed = tuple[object, tuple[int, int], tuple[int, int], float]


@pytest.fixture
def placed(monkeypatch) -> list[Placed]:
    """Record every panel the layout places: where (in screen pixels), how big (in its own) and at which scale."""
    calls: list[Placed] = []
    place = HdLayout._place_scaled

    def record(img, panel, at, size, clock_format, scale=SCALE):  # noqa: PLR0913, PLR0917 -- _place_scaled's signature
        calls.append((panel, at, size, scale))
        place(img, panel, at, size, clock_format, scale)

    monkeypatch.setattr(HdLayout, "_place_scaled", staticmethod(record))
    return calls


def _of(placed: list[Placed], kind: type) -> list[Placed]:
    return [call for call in placed if isinstance(call[0], kind)]


# --- Goldens -------------------------------------------------------------------------------------------------------


def test_the_board_with_everything(snapshot):
    """The header line, the worst notice, the two stops as a board, and the weather, energy and Spotify in the strip."""
    snapshot.assert_matches(
        "screen_hd_landscape_layout_board", _variant("SpotifyGlow").render(_full_panels(), HD_LANDSCAPE).image
    )


def test_the_board_without_spotify_as_the_mockup_has_it(snapshot):
    """The energy chart has the strip beside the weather."""
    img = _variant("Glow").render(_full_panels(), HD_LANDSCAPE).image
    snapshot.assert_matches("screen_hd_landscape_layout_board_no_spotify", img)


def test_the_board_with_spotify_wide_and_the_developer_strip_above_it(snapshot):
    """Without Glowmarkt, Spotify's wide now page has the strip beside the weather, the developer panel over it."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_cover_art_bytes())
    panels = _full_panels() | {
        SPOTIFY.api_name: spotify,
        ClientClasses.DEVELOPER.api_name: DeveloperPanel(DEVELOPER_SUMMARY),
    }
    panels = place_developer_panel(panels, DEVELOPER_AND_SPOTIFY - {GLOW})
    img = _variant("Spotify").render(panels, HD_LANDSCAPE).image
    snapshot.assert_matches("screen_hd_landscape_layout_board_no_energy", img)


def test_the_board_with_neither_and_no_weather_yet(snapshot):
    """No notices: the board starts under the header line. The strip says why there's no weather, and no hours."""
    panels = _full_panels() | {
        ClientClasses.WEATHER.api_name: WeatherClient.message_panel("Could not connect"),
        ClientClasses.NOTICE_BOARD.api_name: NoticeBoardPanel([]),
    }
    img = _variant("Base").render(panels, HD_LANDSCAPE).image
    snapshot.assert_matches("screen_hd_landscape_layout_board_no_weather", img)


# --- Variants ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_each_variant_fits_the_apis_it_shows(unavailable, prefix):
    available = EVERYTHING - unavailable
    fitting = [layout for layout in HD_LANDSCAPE_BOARD_LAYOUTS if layout.fits(available)]

    assert [type(layout).__name__ for layout in fitting] == [f"{prefix}HdLandscapeBoardLayout"]
    [layout] = fitting
    assert layout.layout_id == "board"
    assert layout.canvases == frozenset({HD_LANDSCAPE})
    assert layout.SHOWS_SPOTIFY is (SPOTIFY not in unavailable)
    assert layout.SHOWS_ENERGY is (GLOW not in unavailable)


def test_without_glowmarkt_the_spotify_variant_fits():
    assert [type(layout).__name__ for layout in HD_LANDSCAPE_BOARD_LAYOUTS if layout.fits(NO_GLOWMARKT)] == [
        "SpotifyHdLandscapeBoardLayout"
    ]


@pytest.mark.parametrize("clock_format", ["24h", "12h"])
@pytest.mark.parametrize("panels", [_full_panels, _messages], ids=["full", "messages"])
@pytest.mark.parametrize("layout", HD_LANDSCAPE_BOARD_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_every_variant_draws_an_hd_landscape_frame(layout, panels, clock_format):
    assert layout.render(panels(), HD_LANDSCAPE, clock_format=clock_format).image.size == (1872, 1404)


@pytest.mark.parametrize(
    ("client", "message"),
    [
        (ClientClasses.TFL, DeparturesClient.message_panel("Needs configuring")),
        (GLOW, GlowClient.message_panel("Not configured")),
        (ClientClasses.WEATHER, WeatherClient.message_panel("Could not connect")),
        (SPOTIFY, SpotifyClient.message_panel("Not configured")),
        (ClientClasses.NOTICE_BOARD, NoticeBoardClient.message_panel("Not available")),
    ],
    ids=lambda value: value.api_name if isinstance(value, ClientClasses) else "",
)
@pytest.mark.parametrize("layout", HD_LANDSCAPE_BOARD_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_a_message_in_place_of_any_client_still_draws_a_frame(layout, client, message):
    panels = _full_panels() | {client.api_name: message}

    assert layout.render(panels, HD_LANDSCAPE).image.size == (1872, 1404)


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_a_variant_places_only_what_it_shows(placed, unavailable, prefix):
    energy = _full_panels()[GLOW.api_name]

    _variant(prefix).render(_full_panels(), HD_LANDSCAPE)

    drawn = [panel for panel, *_ in placed]
    assert any(panel is energy for panel in drawn) is (GLOW not in unavailable)
    spotifys = _of(placed, SpotifyPanel)
    assert bool(spotifys) is (SPOTIFY not in unavailable)
    # Spotify's wide now page only with the strip's room to itself.
    assert all(isinstance(panel, HdSpotifyPanel) is (GLOW in unavailable) for panel, *_ in spotifys)
    # With neither, the coming hours have their room.
    assert bool(_of(placed, HdHoursPanel)) is (unavailable == {SPOTIFY, GLOW})


@pytest.mark.parametrize(("_unavailable", "prefix"), VARIANTS)
def test_the_stops_are_drawn_side_by_side_at_the_sidebar_layouts_scale(placed, _unavailable, prefix):
    _variant(prefix).render(_full_panels(), HD_LANDSCAPE)

    boards = _of(placed, BoardStopPanel)
    assert [board.stop.stop_name for board, *_ in boards] == ["Elephant & Castle", "Kennington"]
    assert {scale for *_, scale in boards} == {BOARD_SCALE}
    (left, top), (right, other_top) = (at for _board, at, _size, _scale in boards)
    assert top == other_top
    assert left < right
    # Each as wide as the other, and the two within the screen's width.
    widths = {size[0] for _board, _at, size, _scale in boards}
    assert len(widths) == 1
    assert right + widths.pop() * BOARD_SCALE <= HD_LANDSCAPE.width


def test_the_worst_notice_is_a_banner_over_the_board_and_without_notices_the_board_moves_up(placed):
    layout = _variant("SpotifyGlow")
    layout.render(_full_panels(), HD_LANDSCAPE)
    [(banner, (_x, banner_top), (_width, height), _scale)] = _of(placed, NoticeBannerPanel)
    board_top = _of(placed, BoardStopPanel)[0][1][1]
    assert banner.notices == FEW_NOTICES.notices
    assert banner_top + SCALE * height < board_top

    placed.clear()
    layout.render(_full_panels() | {ClientClasses.NOTICE_BOARD.api_name: NoticeBoardPanel([])}, HD_LANDSCAPE)
    assert not _of(placed, NoticeBannerPanel)
    assert _of(placed, BoardStopPanel)[0][1][1] < board_top


def test_a_message_in_the_stops_place_has_the_whole_board(placed):
    message = DeparturesClient.message_panel("Needs configuring")

    _variant("SpotifyGlow").render(_full_panels() | {ClientClasses.TFL.api_name: message}, HD_LANDSCAPE)

    [(_panel, (x, _y), (width, _height), _scale)] = [call for call in placed if call[0] is message]
    assert not _of(placed, BoardStopPanel)
    assert x * 2 + width * SCALE == HD_LANDSCAPE.width


def test_the_developer_strip_goes_above_spotify_and_the_strip_grows_for_it(placed):
    panels = _full_panels() | {ClientClasses.DEVELOPER.api_name: DeveloperPanel(DEVELOPER_SUMMARY)}
    panels = place_developer_panel(panels, DEVELOPER_AND_SPOTIFY)

    _variant("SpotifyGlow").render(panels, HD_LANDSCAPE)

    [(_developer, (_x, top), (_w, developer_height), _s)] = _of(placed, DeveloperPanel)
    [(_spotify, (_x2, spotify_top), (_w2, spotify_height), _s2)] = _of(placed, SpotifyPanel)
    assert spotify_top == top + SCALE * (developer_height + GAP)
    # Spotify as tall as the rest of the strip, which grows by the developer's strip.
    assert spotify_height == STRIP_HEIGHT
    [(_energy, (_x3, energy_top), (_w3, energy_height), _s3)] = [call for call in placed if call[0] is ENERGY]
    assert energy_top == spotify_top
    assert energy_height == STRIP_HEIGHT


def test_the_header_has_squares_for_the_pages_of_stops(placed):
    stops = _many_route_arrivals()
    paged = CombinedArrivalPanel(stops.arrival_panels, Rotation(page=1, pages=3))

    _variant("SpotifyGlow").render(_full_panels() | {ClientClasses.TFL.api_name: paged}, HD_LANDSCAPE)

    [(header, *_)] = _of(placed, BoardHeaderPanel)
    assert header.squares == (1, 3)


def test_the_weather_message_is_in_the_strip_and_the_header_has_only_the_time(placed):
    message = WeatherClient.message_panel("Could not connect")

    _variant("SpotifyGlow").render(_full_panels() | {ClientClasses.WEATHER.api_name: message}, HD_LANDSCAPE)

    [(header, *_)] = _of(placed, BoardHeaderPanel)
    [(weather, *_)] = _of(placed, BoardWeatherPanel)
    assert header.pages == weather.pages == ()
    assert weather.message == (message.title, message.message)


# --- A stop's board ------------------------------------------------------------------------------------------------


def _stop(rows: list[tuple[str, str, int]]) -> BoardStopPanel:
    arrivals = [
        BusArrival(naptanId="490000123W", lineName=line, timeToStation=minutes * 60, modeName="bus", destinationName=to)
        for line, to, minutes in rows
    ]
    return BoardStopPanel(tfl_panel(BUS_STOP, arrivals))


def test_a_stop_shows_as_many_routes_as_fit():
    stop = _stop([(str(n), "Somewhere", n) for n in range(1, 11)])

    assert stop.rows_fitting(BAR + 4 * ROW + ROW - 1) == 4
    assert stop.rows_fitting(BAR + 40 * ROW) == 10
    assert stop.rows_fitting(BAR - 1) == 0


def test_a_due_departure_and_an_empty_stop_draw(drawn_text):
    due = _stop([("36", "Queen's Park", 0), ("36", "Queen's Park", 9)])
    due.render(380, 200)
    empty = BoardStopPanel(tfl_panel(TUBE_STOP, []))
    empty.render(380, 200)

    texts = [text for text, *_ in drawn_text]
    assert "due" in texts
    assert "then 9 min" in texts
    assert "ELEPHANT & CASTLE" in texts
    assert "KENNINGTON" in texts
    assert "No departures" in texts


def test_the_strips_weather_says_the_days_high_low_and_rain(drawn_text):
    weather = _forecast_weather()

    BoardWeatherPanel(weather.pages).render(230, STRIP_HEIGHT)

    assert "High 18°   Low 9°   Rain 40%" in [text for text, *_ in drawn_text]
