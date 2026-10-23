import countdown_core  # noqa: I001 -- see test_display_snapshots.py for why this comes first
import datetime as dt
import itertools

import pytest
from test_display_snapshots import (
    _FROZEN_NOW,
    DEVELOPER_SUMMARY,
    ENERGY,
    EVERYTHING,
    NOTICES,
    SPOTIFY_QUEUE,
    TUBE_STOP,
    _album_art_bytes,
    _many_route_arrivals,
    _panels,
)
from test_hd_portrait_designs import _forecast_weather
from tfl_factory import tfl_panel

import countdown_core.glow.energy_panel
from countdown_core.core import moment
from countdown_core.core.api_registry import ClientClasses
from countdown_core.departures.departures_client import DeparturesClient
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.glow.glow_client import GlowClient
from countdown_core.newspaper.brief_panel import NewspaperBriefPanel, brief_items, developer_section, music_section
from countdown_core.newspaper.energy_panel import NewspaperEnergyPanel
from countdown_core.newspaper.masthead_panel import NewspaperMastheadPanel, edition
from countdown_core.newspaper.stop_panel import NewspaperStopPanel, deck
from countdown_core.newspaper.typesetting import serif, wrapped
from countdown_core.newspaper.weather_panel import NewspaperWeatherPanel, later_rows, weather_readings
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering.layouts import hd_landscape_newspaper
from countdown_core.rendering.layouts.hd import SCALE, HdLayout
from countdown_core.rendering.layouts.hd_landscape import HD_LANDSCAPE
from countdown_core.rendering.layouts.hd_landscape_newspaper import (
    HD_LANDSCAPE_NEWSPAPER_LAYOUTS,
    HdLandscapeNewspaperLayout,
)
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.tfl.models import TubeArrival
from countdown_core.weather.models import WeatherPeriod
from countdown_core.weather.weather_client import WeatherClient

SPOTIFY, GLOW, DEVELOPER = ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT, ClientClasses.DEVELOPER
# What each variant leaves out, and the start of its class's name.
VARIANTS = [(set(), "SpotifyGlow"), ({GLOW}, "Spotify"), ({SPOTIFY}, "Glow"), ({SPOTIFY, GLOW}, "Base")]
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


def _variant(prefix: str) -> HdLandscapeNewspaperLayout:
    [layout] = [layout for layout in HD_LANDSCAPE_NEWSPAPER_LAYOUTS if type(layout).__name__.startswith(prefix + "Hd")]
    assert isinstance(layout, HdLandscapeNewspaperLayout)
    return layout


def _full_panels(**overrides) -> dict:
    """Everything to show: the stops, a forecast, Spotify playing with the developer panel above it, the notices."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())
    panels = _panels(_many_route_arrivals(), weather=_forecast_weather(), spotify=spotify, notices=NOTICES)
    panels[DEVELOPER.api_name] = DeveloperPanel(DEVELOPER_SUMMARY).compacted()
    return panels | {ClientClasses[name].api_name: panel for name, panel in overrides.items()}


def _messages() -> dict:
    """Return a message in place of every client's panel, and no notices."""
    return {
        ClientClasses.TFL.api_name: DeparturesClient.message_panel("Needs configuring"),
        GLOW.api_name: GlowClient.message_panel("Not configured"),
        ClientClasses.WEATHER.api_name: WeatherClient.message_panel("Could not connect"),
        SPOTIFY.api_name: SpotifyClient.message_panel("Not configured"),
        ClientClasses.NOTICE_BOARD.api_name: NoticeBoardPanel([]),
    }


Placed = tuple[object, tuple[int, int], tuple[int, int], float]


def _placed(monkeypatch) -> list[Placed]:
    """Record every panel the layout places: where (screen pixels), how big (its own) and at which scale."""
    placed: list[Placed] = []
    place = HdLayout._place_scaled

    def record(img, panel, at, size, clock_format, scale=SCALE):  # noqa: PLR0913, PLR0917 -- _place_scaled's signature
        placed.append((panel, at, size, scale))
        place(img, panel, at, size, clock_format, scale)

    monkeypatch.setattr(HdLayout, "_place_scaled", staticmethod(record))
    return placed


def _of(placed: list[Placed], kind: type) -> list[Placed]:
    return [entry for entry in placed if isinstance(entry[0], kind)]


# --- Goldens -------------------------------------------------------------------------------------------------------


def test_the_newspaper_on_an_hd_landscape_screen(snapshot):
    """Everything: the masthead, both stops, the weather, the notices with Spotify and the developer, energy below."""
    img = _variant("SpotifyGlow").render(_full_panels(), HD_LANDSCAPE).image
    assert img.size == (1872, 1404)
    snapshot.assert_matches("screen_hd_landscape_layout_newspaper", img)


def test_the_newspaper_without_spotify_glowmarkt_or_a_forecast(snapshot):
    """The columns run to the foot, the notices have "In brief" to themselves, and the weather says why it's missing."""
    panels = _full_panels(WEATHER=WeatherClient.message_panel("Could not connect"))
    img = _variant("Base").render(panels, HD_LANDSCAPE).image
    snapshot.assert_matches("screen_hd_landscape_layout_newspaper_base_no_weather", img)


# --- The variants --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_each_variant_fits_exactly_the_apis_it_shows(unavailable, prefix):
    layout = _variant(prefix)
    available = EVERYTHING - unavailable

    assert layout.layout_id == "newspaper"
    assert layout.canvases == frozenset({HD_LANDSCAPE})
    assert [type(other).__name__ for other in HD_LANDSCAPE_NEWSPAPER_LAYOUTS if other.fits(available)] == [
        type(layout).__name__
    ]
    assert layout.SHOWS_SPOTIFY is (SPOTIFY not in unavailable)
    assert layout.SHOWS_ENERGY is (GLOW not in unavailable)


def test_the_four_variants_are_everything_first():
    names = [type(layout).__name__ for layout in HD_LANDSCAPE_NEWSPAPER_LAYOUTS]
    assert names == [f"{prefix}HdLandscapeNewspaperLayout" for _unavailable, prefix in VARIANTS]


@pytest.mark.parametrize("client", ["TFL", "GLOWMARKT", "WEATHER", "SPOTIFY", "NOTICE_BOARD", "all"])
@pytest.mark.parametrize("layout", HD_LANDSCAPE_NEWSPAPER_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_a_message_in_place_of_a_client_still_draws_a_whole_frame(layout, client):
    messages = _messages()
    panels = messages if client == "all" else _full_panels(**{client: messages[ClientClasses[client].api_name]})

    assert layout.render(panels, HD_LANDSCAPE).image.size == (1872, 1404)


@pytest.mark.parametrize("layout", HD_LANDSCAPE_NEWSPAPER_LAYOUTS, ids=lambda layout: type(layout).__name__)
def test_every_variant_draws_in_12_hour_time(layout):
    assert layout.render(_full_panels(), HD_LANDSCAPE, clock_format="12h").image.size == HD_LANDSCAPE.size


@pytest.mark.parametrize(("unavailable", "prefix"), VARIANTS)
def test_what_a_variant_does_not_show_is_not_placed(monkeypatch, unavailable, prefix):
    placed = _placed(monkeypatch)
    panels = _full_panels()

    _variant(prefix).render(panels, HD_LANDSCAPE)

    feet = _of(placed, NewspaperEnergyPanel)
    assert bool(feet) is (GLOW not in unavailable)
    assert all(foot.energy is ENERGY for foot, *_ in feet)
    [(brief, *_)] = _of(placed, NewspaperBriefPanel)
    assert brief.notices is panels[ClientClasses.NOTICE_BOARD.api_name]
    if SPOTIFY in unavailable:
        assert (brief.music, brief.developer) == (None, None)
    else:
        assert brief.music is panels[SPOTIFY.api_name]
        assert brief.developer is panels[DEVELOPER.api_name]


def test_the_energy_chart_is_along_the_foot_and_without_it_the_columns_run_down_to_there(monkeypatch):
    placed = _placed(monkeypatch)
    bottom = HD_LANDSCAPE.height - hd_landscape_newspaper.MARGIN

    _variant("SpotifyGlow").render(_full_panels(), HD_LANDSCAPE)
    [(_foot, at, size, _scale)] = _of(placed, NewspaperEnergyPanel)
    assert (at[0], at[1] + SCALE * size[1]) == (hd_landscape_newspaper.MARGIN, bottom)
    [with_energy] = {at[1] + SCALE * size[1] for _panel, at, size, _scale in _of(placed, NewspaperStopPanel)}
    assert with_energy <= at[1] - hd_landscape_newspaper.SECTION_GAP

    placed.clear()
    _variant("Spotify").render(_full_panels(), HD_LANDSCAPE)
    [without] = {at[1] + SCALE * size[1] for _panel, at, size, _scale in _of(placed, NewspaperStopPanel)}
    assert bottom - SCALE < without <= bottom


def test_the_columns_sit_side_by_side_under_the_masthead(monkeypatch):
    placed = _placed(monkeypatch)
    arrivals = _many_route_arrivals()

    _variant("SpotifyGlow").render(_full_panels(TFL=arrivals), HD_LANDSCAPE)

    [(masthead, at, size, _scale)] = _of(placed, NewspaperMastheadPanel)
    assert (masthead.place, at, size[0]) == ("Elephant & Castle", (0, 0), HD_LANDSCAPE.width // SCALE)
    columns = [
        (panel, at, size)
        for panel, at, size, _scale in placed
        if isinstance(panel, NewspaperStopPanel | NewspaperWeatherPanel | NewspaperBriefPanel)
    ]
    assert [type(panel) for panel, *_ in columns] == [
        NewspaperStopPanel,
        NewspaperStopPanel,
        NewspaperWeatherPanel,
        NewspaperBriefPanel,
    ]
    assert [panel.stop for panel, *_ in columns[:2]] == list(arrivals.arrival_panels)
    assert len({at[1] for _panel, at, _size in columns}) == 1
    # Left to right, none overlapping, the last ending at the right margin.
    edges = [(at[0], at[0] + SCALE * size[0]) for _panel, at, size in columns]
    assert all(right <= left for (_left, right), (left, _right) in itertools.pairwise(edges))
    assert edges[0][0] == hd_landscape_newspaper.MARGIN
    assert HD_LANDSCAPE.width - hd_landscape_newspaper.MARGIN - edges[-1][1] in {0, 1}


def test_a_lone_stop_has_both_stops_width(monkeypatch):
    placed = _placed(monkeypatch)
    [first, _second] = _many_route_arrivals().arrival_panels

    _variant("Base").render(_full_panels(TFL=CombinedArrivalPanel([first])), HD_LANDSCAPE)

    [(stop, _at, size, _scale)] = _of(placed, NewspaperStopPanel)
    assert (stop.stop, size[0]) == (first, hd_landscape_newspaper.STOPS_WIDTH)


def test_without_stops_the_masthead_has_its_own_name_and_the_column_says_why(monkeypatch):
    placed = _placed(monkeypatch)
    message = DeparturesClient.message_panel("Needs configuring")

    _variant("Base").render(_full_panels(TFL=message), HD_LANDSCAPE)

    [(masthead, *_)] = _of(placed, NewspaperMastheadPanel)
    assert masthead.place is None
    assert masthead.name == "THE DAILY COUNTDOWN"
    [(stop, *_)] = _of(placed, NewspaperStopPanel)
    assert stop.stop is message


def test_the_developer_panel_in_spotifys_place_is_in_brief(monkeypatch):
    placed = _placed(monkeypatch)
    developer = DeveloperPanel(DEVELOPER_SUMMARY)
    panels = {name: panel for name, panel in _full_panels(SPOTIFY=developer).items() if name != DEVELOPER.api_name}

    _variant("SpotifyGlow").render(panels, HD_LANDSCAPE)

    [(brief, *_)] = _of(placed, NewspaperBriefPanel)
    assert brief.developer is developer


def test_the_weather_column_has_the_forecasts_pages_or_its_message(monkeypatch):
    placed = _placed(monkeypatch)
    weather = _forecast_weather()
    layout = _variant("Base")

    layout.render(_full_panels(WEATHER=weather), HD_LANDSCAPE)
    layout.render(_full_panels(WEATHER=WeatherClient.message_panel("Could not connect")), HD_LANDSCAPE)

    [(forecast, *_), (missing, *_)] = _of(placed, NewspaperWeatherPanel)
    assert (forecast.pages, forecast.message) == (tuple(weather.pages), None)
    assert (missing.pages, missing.message) == ((), ("Weather", "Could not connect"))


# --- The panels ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("hour", "expected"), [(0, "Morning"), (11, "Morning"), (12, "Afternoon"), (17, "Late")])
def test_the_edition_goes_by_the_time_of_day(hour, expected):
    assert edition(hour) == f"{expected} edition"


def test_the_masthead_is_named_for_the_place_in_capitals():
    assert NewspaperMastheadPanel("Elephant & Castle").name == "THE ELEPHANT & CASTLE"


def test_the_deck_says_what_leaves_from_the_stop():
    bus, tube = _many_route_arrivals().arrival_panels
    two_lines = tfl_panel(
        TUBE_STOP,
        [
            TubeArrival(naptanId="940GZZLUKNG", lineName=line, timeToStation=60, modeName="tube", towards="Somewhere")
            for line in ("Northern", "Bakerloo")
        ],
    )

    assert deck(bus) == "Buses, departing"
    assert deck(tube) == "Northern line, departing"
    assert deck(two_lines) == "Northern & Bakerloo lines, departing"


def test_the_weather_readings_come_most_wanted_first():
    now = _forecast_weather().weather
    labels = [label for label, _value in weather_readings(now, "24h")]

    assert labels[:4] == ["High", "Low", "Rain", "Sunset"]


def test_the_later_rows_are_the_forecasts_pages_after_now_tomorrow_with_its_high_and_low():
    pages = _forecast_weather().pages
    tomorrow = next(page for page in pages if page.period is WeatherPeriod.TOMORROW)

    rows = later_rows(pages)

    assert [title for title, *_ in rows] == ["Tonight", "Tomorrow", "Weekend"]
    assert rows[1][2] == f"{round(tomorrow.high)}° / {round(tomorrow.low)}°"


def test_a_lone_weather_page_has_what_comes_next_instead():
    lone = _forecast_weather().weather
    [(title, _condition, temperature, _code, _is_day)] = later_rows([lone])

    assert title == lone.upcoming_weather_code[0]
    assert temperature == ""


def test_in_brief_has_planned_works_after_the_rest_and_a_message_as_one_item():
    planned = Notice("Central", Severity.PLANNED, "No service on Saturday")
    severe = Notice("Met Office", Severity.SEVERE, "Red warning")

    assert brief_items(NoticeBoardPanel([planned, severe]), "24h") == [
        ("Met Office", "Red warning", False),
        ("Central", "No service on Saturday", True),
    ]
    assert brief_items(None, "24h") == []
    message = SpotifyClient.message_panel("Not configured")
    assert brief_items(message, "24h") == [("Spotify", "Not configured", False)]


def test_in_brief_has_the_song_and_whats_next_but_not_spotifys_message():
    assert music_section(SpotifyPanel(SPOTIFY_QUEUE)) == (
        "On the turntable",
        [
            "\N{LEFT DOUBLE QUOTATION MARK}Around The World\N{RIGHT DOUBLE QUOTATION MARK}",
            "Daft Punk",
            "Up next: Da Funk",
        ],
    )
    assert music_section(SpotifyClient.message_panel("Not configured")) is None


def test_in_brief_has_the_pull_requests_and_the_builds():
    assert developer_section(DeveloperPanel(DEVELOPER_SUMMARY), 174) == (
        "At the workshop",
        ["3 open PRs, 1 to review", "auth-broker failing"],
    )
    no_github = DeveloperPanel(DEVELOPER_SUMMARY.model_copy(update={"github": None}))
    heading, lines = developer_section(no_github, 174)
    assert heading == "At the workshop"
    assert " ".join(lines) == "Add a GitHub token on the config page"


def test_wrapping_cuts_the_last_line_short():
    font = serif(13)
    lines = wrapped("Severe delays due to a signal failure at Camden Town.", font, 174, 2)

    assert len(lines) == 2
    assert lines[-1].endswith("\N{HORIZONTAL ELLIPSIS}")
    assert all(font.getlength(line) <= 174 for line in lines)
