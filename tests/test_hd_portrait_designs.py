import countdown_core  # noqa: I001 -- see test_display_snapshots.py for why this comes first
import datetime as dt
from typing import TYPE_CHECKING

import pytest
from tfl_factory import tfl_panel
from weather_factory import forecast_json, make_weather

import countdown_core.glow.energy_panel
from countdown_api.models import Screen
from countdown_core.core import moment
from countdown_core.core.api_registry import ClientClasses
from countdown_core.departures.departures_client import DeparturesClient
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.glow.glow_client import GlowClient
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import NoticeBannerPanel, NoticeBoardPanel
from countdown_core.rendering import LAYOUTS, layout_for
from countdown_core.rendering.layouts import hd_portrait_designs
from countdown_core.rendering.layouts.classic import SpotifyGlowLayout
from countdown_core.rendering.layouts.hd import SCALE, HdLayout, coming_hours, stops_of, weather_parts
from countdown_core.rendering.layouts.hd_landscape import HD_LANDSCAPE, SpotifyGlowHdLandscapeLayout
from countdown_core.rendering.layouts.hd_portrait import (
    HD_PORTRAIT,
    BaseHdPortraitLayout,
    GlowHdPortraitLayout,
    SpotifyGlowHdPortraitLayout,
    SpotifyHdPortraitLayout,
)
from countdown_core.rendering.layouts.hd_portrait_designs import (
    CARD_PADDING,
    HD_PORTRAIT_DESIGNS,
    MARGIN,
    STACKED_SCALE,
    HdBoardLayout,
    HdColumnLayout,
    HdLockScreenLayout,
    HdMastheadLayout,
    HdPagesLayout,
    HdTimelineLayout,
    next_departures,
)
from countdown_core.rendering.screen import DEFAULT_CANVAS
from countdown_core.spotify.models import Queue
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_core.tfl.route_board_panel import RouteBoardPanel
from countdown_core.tfl.timeline_panel import TimelinePanel
from countdown_core.weather.hd_clock_panels import HdClockPanel, HdMastheadPanel, HdWeatherColumnPanel
from countdown_core.weather.hd_sidebar_panel import HdHoursPanel
from countdown_core.weather.hd_weather_panel import HdWeatherPanel
from countdown_core.weather.models import ForecastResponse
from countdown_core.weather.weather_client import WeatherClient
from countdown_core.weather.weather_forecast import weather_pages
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.rendering.layout import Layout

EVERYTHING = frozenset(ClientClasses) - {ClientClasses.DEVELOPER}
SPOTIFY, GLOW = ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT
DESIGNS = [
    ("masthead", HdMastheadLayout),
    ("column", HdColumnLayout),
    ("lockscreen", HdLockScreenLayout),
    ("timeline", HdTimelineLayout),
    ("board", HdBoardLayout),
    ("pages", HdPagesLayout),
]
VARIANTS = [
    (set(), "SpotifyGlow"),
    ({GLOW}, "Spotify"),
    ({SPOTIFY}, "Glow"),
    ({SPOTIFY, GLOW}, "Base"),
]
# Naive, as the energy panel's clock is; the clock panels draw the render's moment, aware.
_FROZEN_NOW = dt.datetime(2026, 1, 15, 12, 0, 0)  # noqa: DTZ001 -- see above
_MOMENT = _FROZEN_NOW.replace(tzinfo=dt.UTC)


class _FrozenDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    monkeypatch.setattr(countdown_core.glow.energy_panel, "datetime", _FrozenDatetime)
    with moment.drawing_at(_MOMENT):
        yield


def _hd_screen(layout: str | None, orientation: str = "vertical") -> Screen:
    return Screen(orientation=orientation, width=1872, height=1404, format="png", layout=layout)


# --- Choosing a design ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
@pytest.mark.parametrize(("layout_id", "design"), DESIGNS)
def test_the_config_picks_each_hd_portrait_design_in_the_variant_for_what_can_be_shown(
    layout_id, design, unavailable, prefix
):
    layout, canvas = layout_for(_hd_screen(layout_id), EVERYTHING - unavailable)

    assert canvas == HD_PORTRAIT
    assert isinstance(layout, design)
    assert type(layout).__name__ == f"{prefix}{design.__name__}"
    assert layout.layout_id == layout_id
    assert layout.SHOWS_SPOTIFY is (SPOTIFY not in unavailable)
    assert layout.SHOWS_ENERGY is (GLOW not in unavailable)


@pytest.mark.parametrize(
    ("unavailable", "expected"),
    [
        (set(), SpotifyGlowHdPortraitLayout),
        ({GLOW}, SpotifyHdPortraitLayout),
        ({SPOTIFY}, GlowHdPortraitLayout),
        ({SPOTIFY, GLOW}, BaseHdPortraitLayout),
    ],
)
@pytest.mark.parametrize("layout_id", [None, "classic"])
def test_no_layout_chosen_gives_the_classic_hd_portrait_layout(layout_id, unavailable, expected):
    layout, canvas = layout_for(_hd_screen(layout_id), EVERYTHING - unavailable)

    assert canvas == HD_PORTRAIT
    assert type(layout) is expected


@pytest.mark.parametrize(
    "screen",
    [
        _hd_screen("sidebar"),  # the landscape screen's
        Screen.model_construct(orientation="vertical", width=1872, height=1404, format="png", layout="nonsense"),
    ],
    ids=["sidebar", "unknown"],
)
def test_a_layout_the_hd_portrait_canvas_does_not_have_falls_back_to_its_classic_layout(screen):
    layout, canvas = layout_for(screen, EVERYTHING)

    assert canvas == HD_PORTRAIT
    assert type(layout) is SpotifyGlowHdPortraitLayout


def test_a_portrait_design_chosen_for_a_landscape_screen_gets_the_landscape_default():
    layout, canvas = layout_for(_hd_screen("masthead", "horizontal"), EVERYTHING)

    assert canvas == HD_LANDSCAPE
    assert type(layout) is SpotifyGlowHdLandscapeLayout


def test_a_portrait_design_chosen_for_a_small_screen_gets_the_classic_layout():
    screen = Screen(orientation="horizontal", width=800, height=480, format="mono-1bpp-packed", layout="masthead")

    layout, canvas = layout_for(screen, EVERYTHING)

    assert canvas == DEFAULT_CANVAS
    assert type(layout) is SpotifyGlowLayout


def test_the_developer_panel_makes_a_design_lay_out_spotifys_area():
    layout, _canvas = layout_for(_hd_screen("board"), EVERYTHING - {SPOTIFY} | {ClientClasses.DEVELOPER})

    assert layout.SHOWS_SPOTIFY


def test_the_hd_portrait_canvas_offers_the_classic_layout_first_then_the_six_designs_in_order():
    assert LAYOUTS.layout_ids(HD_PORTRAIT) == ["classic", *(layout_id for layout_id, _design in DESIGNS)]


def test_the_designs_draw_only_on_the_hd_portrait_canvas():
    assert all(layout.canvases == frozenset({HD_PORTRAIT}) for layout in HD_PORTRAIT_DESIGNS)
    assert not {"masthead", "lockscreen", "timeline"} & set(LAYOUTS.layout_ids(HD_LANDSCAPE))


@pytest.mark.parametrize(("layout_id", "design"), DESIGNS)
def test_every_combination_of_spotify_and_glowmarkt_has_exactly_one_variant_of_each_design(layout_id, design):
    variants = [layout for layout in HD_PORTRAIT_DESIGNS if layout.layout_id == layout_id]
    assert len(variants) == 4
    assert all(isinstance(layout, design) for layout in variants)
    for unavailable, _prefix in VARIANTS:
        assert len([layout for layout in variants if layout.fits(EVERYTHING - unavailable)]) == 1


# --- Drawing -------------------------------------------------------------------------------------------------------

BUS_STOP = SingleStopPoint(
    naptanId="490000123W",
    commonName="Elephant & Castle",
    modes=["bus"],
    additionalProperties=[],
    stopType="NaptanPublicBusCoachTram",
    stopLetter="W",
)
TUBE_STOP = MetroStopPoint(
    naptanId="940GZZLUKNG",
    commonName="Kennington Underground Station",
    modes=["tube"],
    additionalProperties=[],
    stopType="NaptanMetroStation",
)


def _buses(rows: list[tuple[str, str, int]]) -> list[BusArrival]:
    return [
        BusArrival(naptanId="490000123W", lineName=line, timeToStation=minutes * 60, modeName="bus", destinationName=to)
        for line, to, minutes in rows
    ]


def _trains(rows: list[tuple[str, int]]) -> list[TubeArrival]:
    return [
        TubeArrival(
            naptanId="940GZZLUKNG", lineName="Northern", timeToStation=minutes * 60, modeName="tube", towards=to
        )
        for to, minutes in rows
    ]


BUSY_BUS_ARRIVALS = _buses(
    [
        ("36", "Queen's Park", 2),
        ("185", "Victoria", 4),
        ("36", "Queen's Park", 9),
        ("436", "Paddington", 11),
        ("N155", "Aldwych", 14),
        ("12", "Oxford Circus", 19),
        ("148", "White City", 21),
        ("171", "Holborn", 23),
    ]
)
BUSY_TUBE_ARRIVALS = _trains(
    [("Morden", 1), ("Edgware via Bank", 3), ("Battersea Power Station", 5), ("Kennington", 14), ("Morden", 9)]
)


def _many_route_arrivals(style: str = "hero") -> CombinedArrivalPanel:
    return CombinedArrivalPanel(
        [tfl_panel(BUS_STOP, BUSY_BUS_ARRIVALS, style), tfl_panel(TUBE_STOP, BUSY_TUBE_ARRIVALS, style)]
    )


def _forecast_weather(design: str = "columns") -> WeatherPanel:
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    return WeatherPanel(pages[0], design, pages)


def _queue() -> Queue:
    track = {"name": "A Song", "artists": [{"name": "An Artist"}], "album": {"name": "An Album", "images": []}}
    return Queue.model_validate({"currently_playing": {**track, "duration_ms": 1}, "queue": []})


ENERGY = EnergyPanel(
    [
        {"start": (_FROZEN_NOW - dt.timedelta(hours=23 - hour)).isoformat(), "kwh": 0.4 + (hour % 6) * 0.25}
        for hour in range(24)
    ],
    0,
)
NOTICES = NoticeBoardPanel(
    [
        Notice("Met Office", Severity.SEVERE, "Red warning: wind until Fri 21:00"),
        Notice("Northern", Severity.WARNING, "Severe delays due to a signal failure at Camden Town."),
        Notice("Circle", Severity.INFO, "Issues Reported"),
    ]
)


def _full_panels(arrivals: Panel | None = None, weather: Panel | None = None) -> dict[str, Panel]:
    return {
        ClientClasses.TFL.api_name: arrivals if arrivals is not None else _many_route_arrivals(),
        GLOW.api_name: ENERGY,
        ClientClasses.WEATHER.api_name: weather if weather is not None else _forecast_weather(),
        SPOTIFY.api_name: SpotifyPanel(_queue()),
        ClientClasses.NOTICE_BOARD.api_name: NOTICES,
    }


def _message_panels() -> dict[str, Panel]:
    """Return a message in place of every client's panel, and no notices."""
    return {
        ClientClasses.TFL.api_name: DeparturesClient.message_panel("Needs configuring"),
        GLOW.api_name: GlowClient.message_panel("Not configured"),
        ClientClasses.WEATHER.api_name: WeatherClient.message_panel("Could not connect"),
        SPOTIFY.api_name: SpotifyClient.message_panel("Not configured"),
        ClientClasses.NOTICE_BOARD.api_name: NoticeBoardPanel([]),
    }


@pytest.mark.parametrize("minute", [0, 1], ids=["page 0", "page 1"])
@pytest.mark.parametrize("panels", [_full_panels, _message_panels], ids=["full", "messages"])
@pytest.mark.parametrize("layout", HD_PORTRAIT_DESIGNS, ids=lambda layout: type(layout).__name__)
def test_every_design_and_variant_draws_an_hd_portrait_frame(layout, panels, minute, rotation_clock):
    rotation_clock.at_minute(minute)

    frame = layout.render(panels(), HD_PORTRAIT)

    assert frame.image.size == (1404, 1872)


@pytest.mark.parametrize("layout", HD_PORTRAIT_DESIGNS, ids=lambda layout: type(layout).__name__)
def test_every_design_draws_in_12_hour_time(layout, rotation_clock):
    rotation_clock.at_minute(1)
    assert layout.render(_full_panels(), HD_PORTRAIT, clock_format="12h").image.size == HD_PORTRAIT.size


Placed = tuple["Panel", tuple[int, int], tuple[int, int], float]


def _placed(monkeypatch) -> list[Placed]:
    """Record every panel an HD layout places: where (in screen pixels), how big (in its own) and at which scale."""
    placed: list[Placed] = []
    place = HdLayout._place_scaled

    def record(img, panel, at, size, clock_format, scale=SCALE):  # noqa: PLR0913, PLR0917 -- _place_scaled's signature
        placed.append((panel, at, size, scale))
        place(img, panel, at, size, clock_format, scale)

    monkeypatch.setattr(HdLayout, "_place_scaled", staticmethod(record))
    return placed


def _variant(layout_id: str, prefix: str) -> Layout:
    [layout] = [
        layout
        for layout in HD_PORTRAIT_DESIGNS
        if layout.layout_id == layout_id and type(layout).__name__.startswith(f"{prefix}Hd")
    ]
    return layout


def _stops_room(placed: list[Placed]) -> int:
    """How many screen pixels tall the stops are drawn, all told (on the pages layout's home page, the weather)."""
    stops = (AbstractArrivalPanel, CombinedArrivalPanel, TimelinePanel, RouteBoardPanel, HdWeatherPanel)
    return sum(round(size[1] * scale) for panel, _at, size, scale in placed if isinstance(panel, stops))


@pytest.mark.parametrize("minute", [0, 1], ids=["page 0", "page 1"])
@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
@pytest.mark.parametrize(("layout_id", "_design"), DESIGNS)
def test_a_design_places_spotify_and_the_energy_chart_only_in_the_variants_that_show_them(  # noqa: PLR0913, PLR0917 -- the parameters
    monkeypatch, rotation_clock, layout_id, _design, unavailable, prefix, minute
):
    rotation_clock.at_minute(minute)
    placed = _placed(monkeypatch)
    spotify = SpotifyPanel(_queue())
    panels = _full_panels() | {SPOTIFY.api_name: spotify}

    _variant(layout_id, prefix).render(panels, HD_PORTRAIT)

    drawn = [panel for panel, *_ in placed]
    # The pages layout draws them on its home page only.
    going_out = layout_id == "pages" and minute == 0
    assert any(panel is ENERGY for panel in drawn) is (GLOW not in unavailable and not going_out)
    spotifys = [panel for panel in drawn if isinstance(panel, SpotifyPanel)]
    assert bool(spotifys) is (SPOTIFY not in unavailable and not going_out)
    assert all(panel.queue is spotify.queue for panel in spotifys)


@pytest.mark.parametrize(("layout_id", "_design"), DESIGNS)
def test_the_stops_get_the_room_spotify_and_the_energy_chart_leave(monkeypatch, layout_id, _design):
    placed = _placed(monkeypatch)
    rooms = {}
    for _unavailable, prefix in VARIANTS:
        placed.clear()
        _variant(layout_id, prefix).render(_full_panels(), HD_PORTRAIT)
        rooms[prefix] = _stops_room(placed)

    if layout_id == "pages":  # going out, with neither on the page: the stops' room is the same in each variant
        assert len(set(rooms.values())) == 1
        return
    assert rooms["Base"] > rooms["SpotifyGlow"]
    assert rooms["Base"] == max(rooms.values())


def test_the_column_gives_the_stops_spotifys_room_and_the_energy_charts_each(monkeypatch):
    placed = _placed(monkeypatch)
    rooms = {}
    for _unavailable, prefix in VARIANTS:
        placed.clear()
        _variant("column", prefix).render(_full_panels(), HD_PORTRAIT)
        rooms[prefix] = _stops_room(placed)

    assert rooms["SpotifyGlow"] < rooms["Spotify"] < rooms["Base"]
    assert rooms["SpotifyGlow"] < rooms["Glow"] < rooms["Base"]


def test_the_lock_screen_keeps_the_hours_card_without_the_energy_chart(monkeypatch):
    """The widgets' row stays for the hours, so only Spotify's card gives the stops room."""
    placed = _placed(monkeypatch)
    rooms, hours = {}, {}
    for _unavailable, prefix in VARIANTS:
        placed.clear()
        _variant("lockscreen", prefix).render(_full_panels(), HD_PORTRAIT)
        rooms[prefix] = _stops_room(placed)
        [hours[prefix]] = [panel.hours for panel, *_ in placed if isinstance(panel, HdHoursPanel)]

    assert rooms["SpotifyGlow"] == rooms["Spotify"] < rooms["Glow"] == rooms["Base"]
    every_hour = coming_hours(_full_panels()[ClientClasses.WEATHER.api_name])
    assert hours["SpotifyGlow"] == every_hour[: hd_portrait_designs.WIDGET_HOURS]
    assert hours["Base"] == every_hour


def test_the_home_pages_weather_gets_the_room_spotify_and_the_energy_chart_leave_up_to_its_most(
    monkeypatch, rotation_clock
):
    rotation_clock.at_minute(1)
    placed = _placed(monkeypatch)
    rooms = {}
    for _unavailable, prefix in VARIANTS:
        placed.clear()
        _variant("pages", prefix).render(_full_panels(), HD_PORTRAIT)
        rooms[prefix] = _stops_room(placed)

    assert rooms["SpotifyGlow"] < rooms["Base"]
    assert max(rooms.values()) == pytest.approx(hd_portrait_designs.HOME_WEATHER_HEIGHT, abs=2)


# --- Each design's own -----------------------------------------------------------------------------------------------


def test_the_masthead_has_the_weathers_pages_across_the_top_and_the_stops_side_by_side_under_it(monkeypatch):
    placed = _placed(monkeypatch)
    weather, arrivals = _forecast_weather(), _many_route_arrivals()

    _variant("masthead", "Base").render(_full_panels(arrivals, weather), HD_PORTRAIT)

    [(band, at, size, _scale)] = [entry for entry in placed if isinstance(entry[0], HdMastheadPanel)]
    assert (band.pages, band.message) == (tuple(weather.pages), None)
    assert (at, size) == ((0, 0), (HD_PORTRAIT.width // SCALE, hd_portrait_designs.MASTHEAD_HEIGHT))
    [(_panel, at, _size, scale)] = [entry for entry in placed if entry[0] is arrivals]
    assert at == (MARGIN, SCALE * hd_portrait_designs.MASTHEAD_HEIGHT + hd_portrait_designs.SECTION_GAP)
    assert scale == hd_portrait_designs.ARRIVALS_SCALE


def test_without_a_forecast_the_clock_panels_say_why(monkeypatch, rotation_clock):
    placed = _placed(monkeypatch)
    panels = _full_panels(weather=WeatherClient.message_panel("Could not connect"))

    for layout_id in ("masthead", "column", "board"):
        _variant(layout_id, "Base").render(panels, HD_PORTRAIT)

    clocks = [panel for panel, *_ in placed if isinstance(panel, HdMastheadPanel | HdWeatherColumnPanel | HdClockPanel)]
    assert {type(panel) for panel in clocks} == {HdMastheadPanel, HdWeatherColumnPanel, HdClockPanel}
    assert all((panel.pages, panel.message) == ((), ("Weather", "Could not connect")) for panel in clocks)
    assert not any(isinstance(panel, HdHoursPanel) for panel, *_ in placed)


def test_weather_parts_are_the_pages_or_the_message_or_neither():
    weather = _forecast_weather()
    lone = WeatherPanel(make_weather())

    assert weather_parts(weather) == (tuple(weather.pages), None)
    assert weather_parts(lone) == ((lone.weather,), None)
    assert weather_parts(MessagePanel("Weather", "Could not connect")) == ((), ("Weather", "Could not connect"))
    assert weather_parts(NoticeBoardPanel([])) == ((), None)


def test_coming_hours_come_from_the_first_page_with_any():
    weather = _forecast_weather()

    assert coming_hours(weather) == weather.pages[0].hours
    assert coming_hours(WeatherPanel(make_weather())) == ()
    assert coming_hours(MessagePanel("Weather", "Could not connect")) == ()


def test_stops_of_are_each_stop_or_the_message_itself():
    arrivals = _many_route_arrivals()
    message = DeparturesClient.message_panel("Needs configuring")

    assert stops_of(arrivals) == list(arrivals.arrival_panels)
    assert stops_of(message) == [message]


def test_the_column_has_the_weather_down_its_left_and_the_stops_one_under_the_other_beside_it(monkeypatch):
    placed = _placed(monkeypatch)
    arrivals = _many_route_arrivals()

    _variant("column", "Base").render(_full_panels(arrivals), HD_PORTRAIT)

    [(_column, at, size, _scale)] = [entry for entry in placed if isinstance(entry[0], HdWeatherColumnPanel)]
    assert at == (0, hd_portrait_designs.EDGE)
    assert size[0] == hd_portrait_designs.COLUMN_WIDTH
    stops = [(at, size, scale) for panel, at, size, scale in placed if panel in arrivals.arrival_panels]
    assert len(stops) == 2
    (first_at, first_size, scale), (second_at, _size, _scale) = stops
    assert scale == STACKED_SCALE
    assert first_at[0] == second_at[0] == SCALE * hd_portrait_designs.COLUMN_WIDTH + hd_portrait_designs.SECTION_GAP
    assert second_at[1] > first_at[1] + first_size[1] * STACKED_SCALE


def test_on_the_lock_screen_a_stop_in_the_card_style_is_not_wrapped_in_another_card(monkeypatch):
    placed = _placed(monkeypatch)
    drawn = {}
    for style in ("hero", "card"):
        placed.clear()
        arrivals = _many_route_arrivals(style)
        _variant("lockscreen", "Base").render(_full_panels(arrivals), HD_PORTRAIT)
        # Where each stop is, and how wide in screen pixels.
        drawn[style] = [
            (at, round(size[0] * scale)) for panel, at, size, scale in placed if panel in arrivals.arrival_panels
        ]

    assert len(drawn["hero"]) == len(drawn["card"]) == 2
    assert all(at[0] == MARGIN for at, _width in drawn["card"])
    assert all(width == pytest.approx(HD_PORTRAIT.width - 2 * MARGIN, abs=2) for _at, width in drawn["card"])
    # In a card: inside its padding, either side.
    for (hero_at, hero_width), (card_at, card_width) in zip(drawn["hero"], drawn["card"], strict=True):
        assert (hero_at[0] - card_at[0], hero_at[1] - card_at[1]) == (CARD_PADDING, CARD_PADDING)
        assert card_width - hero_width == pytest.approx(2 * CARD_PADDING, abs=2)


def test_the_lock_screen_draws_a_card_for_each_stop_only_when_it_has_none_of_its_own(monkeypatch):
    cards = []
    card = hd_portrait_designs._card

    def record(img, box):
        cards.append(box)
        return card(img, box)

    monkeypatch.setattr(hd_portrait_designs, "_card", record)
    layout = _variant("lockscreen", "SpotifyGlow")
    counts = {}
    for style in ("hero", "card"):
        cards.clear()
        layout.render(_full_panels(_many_route_arrivals(style)), HD_PORTRAIT)
        counts[style] = len(cards)

    assert counts["hero"] - counts["card"] == 2
    assert counts["card"] == 4  # the notices, the energy chart, the hours and Spotify


def test_the_timeline_draws_the_first_two_stops_under_the_worst_notice_as_a_banner(monkeypatch):
    placed = _placed(monkeypatch)
    third = tfl_panel(BUS_STOP.model_copy(update={"common_name": "Walworth Road"}), BUSY_BUS_ARRIVALS)
    arrivals = CombinedArrivalPanel([*_many_route_arrivals().arrival_panels, third])

    _variant("timeline", "Base").render(_full_panels(arrivals), HD_PORTRAIT)

    [timeline] = [panel for panel, *_ in placed if isinstance(panel, TimelinePanel)]
    assert timeline.stops == list(arrivals.arrival_panels[:2])
    [banner] = [panel for panel, *_ in placed if isinstance(panel, NoticeBannerPanel)]
    assert banner.notices == NOTICES.notices
    assert not any(isinstance(panel, NoticeBoardPanel) for panel, *_ in placed)  # the board itself isn't drawn


def test_the_timeline_with_no_notices_has_no_banner_and_the_line_starts_higher(monkeypatch):
    placed = _placed(monkeypatch)
    layout = _variant("timeline", "Base")

    layout.render(_full_panels(), HD_PORTRAIT)
    [with_banner] = [at for panel, at, *_ in placed if isinstance(panel, TimelinePanel)]
    placed.clear()
    layout.render(_full_panels() | {ClientClasses.NOTICE_BOARD.api_name: NoticeBoardPanel([])}, HD_PORTRAIT)
    [without] = [at for panel, at, *_ in placed if isinstance(panel, TimelinePanel)]

    assert not any(isinstance(panel, NoticeBannerPanel) for panel, *_ in placed)
    assert with_banner[1] - without[1] == SCALE * NoticeBannerPanel.HEIGHT


def test_the_board_draws_every_stop_as_a_departures_board(monkeypatch):
    placed = _placed(monkeypatch)
    arrivals = _many_route_arrivals()

    _variant("board", "SpotifyGlow").render(_full_panels(arrivals), HD_PORTRAIT)

    [(board, at, size, scale)] = [entry for entry in placed if isinstance(entry[0], RouteBoardPanel)]
    assert board.stops == list(arrivals.arrival_panels)
    assert at == (MARGIN, SCALE * hd_portrait_designs.HEADER_HEIGHT + hd_portrait_designs.SECTION_GAP)
    assert (size[0], scale) == ((HD_PORTRAIT.width - 2 * MARGIN) // SCALE, SCALE)


# --- The pages layout ------------------------------------------------------------------------------------------------


def test_the_pages_layout_goes_out_on_even_minutes(monkeypatch, rotation_clock):
    rotation_clock.at_minute(0)
    placed = _placed(monkeypatch)
    arrivals = _many_route_arrivals()

    _variant("pages", "SpotifyGlow").render(_full_panels(arrivals), HD_PORTRAIT)

    [header] = [panel for panel, *_ in placed if isinstance(panel, HdClockPanel)]
    assert (header.squares, header.summary) == ((0, 2), None)
    assert [panel for panel, *_ in placed if isinstance(panel, AbstractArrivalPanel)] == list(arrivals.arrival_panels)
    assert any(isinstance(panel, HdHoursPanel) for panel, *_ in placed)
    assert not any(isinstance(panel, HdWeatherPanel | SpotifyPanel) or panel is ENERGY for panel, *_ in placed)


def test_the_pages_layout_is_at_home_on_odd_minutes(monkeypatch, rotation_clock):
    rotation_clock.at_minute(3)
    placed = _placed(monkeypatch)
    arrivals, weather = _many_route_arrivals(), _forecast_weather("poster")

    _variant("pages", "SpotifyGlow").render(_full_panels(arrivals, weather), HD_PORTRAIT)

    [header] = [panel for panel, *_ in placed if isinstance(panel, HdClockPanel)]
    assert header.squares == (1, 2)
    assert header.summary == "36 in 2 min   N in 1 min"
    [(hd, _at, _size, scale)] = [entry for entry in placed if isinstance(entry[0], HdWeatherPanel)]
    assert (hd.weather, hd.design, hd.pages) == (weather.weather, "poster", tuple(weather.pages))
    assert scale == hd_portrait_designs.HOME_WEATHER_SCALE
    assert not any(isinstance(panel, AbstractArrivalPanel) for panel, *_ in placed)
    assert any(panel is ENERGY for panel, *_ in placed)


def test_the_home_page_leaves_out_the_hours_strip_under_the_timeline_weather_which_has_them(
    monkeypatch, rotation_clock
):
    rotation_clock.at_minute(1)
    placed = _placed(monkeypatch)
    layout = _variant("pages", "Base")

    layout.render(_full_panels(weather=_forecast_weather("timeline")), HD_PORTRAIT)
    assert not any(isinstance(panel, HdHoursPanel) for panel, *_ in placed)
    placed.clear()
    layout.render(_full_panels(weather=_forecast_weather("poster")), HD_PORTRAIT)
    assert any(isinstance(panel, HdHoursPanel) for panel, *_ in placed)


def test_the_home_page_draws_the_weathers_message_in_its_place(monkeypatch, rotation_clock):
    rotation_clock.at_minute(1)
    placed = _placed(monkeypatch)
    message = WeatherClient.message_panel("Could not connect")

    _variant("pages", "Base").render(_full_panels(weather=message), HD_PORTRAIT)

    [(_panel, at, _size, scale)] = [entry for entry in placed if entry[0] is message]
    assert at == (MARGIN, SCALE * hd_portrait_designs.HEADER_HEIGHT + hd_portrait_designs.SECTION_GAP)
    assert scale == SCALE
    assert not any(isinstance(panel, HdWeatherPanel | HdHoursPanel) for panel, *_ in placed)


def test_next_departures_has_each_stops_soonest_in_brief():
    buses = tfl_panel(BUS_STOP, _buses([("185", "Victoria", 4), ("36", "Queen's Park", 0), ("36", "Queen's Park", 6)]))
    trains = tfl_panel(TUBE_STOP, _trains([("Morden", 7), ("Edgware via Bank", 2)]))

    assert next_departures([buses, trains]) == "36 due   N in 2 min"


def test_next_departures_skips_messages_and_stops_with_nothing_leaving():
    trains = tfl_panel(TUBE_STOP, _trains([("Morden", 12)]))
    empty = tfl_panel(BUS_STOP, [])

    assert next_departures([DeparturesClient.message_panel("Needs configuring"), empty, trains]) == "N in 12 min"
    assert next_departures([empty]) == ""
    assert next_departures([]) == ""
