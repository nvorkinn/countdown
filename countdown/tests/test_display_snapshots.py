"""Visual regression tests for the composed display screen: each scenario builds panels
from representative data (no HTTP mocking -- see test_e2e_render_cycle.py) and compares
the render against a golden image in tests/images/. See the README for the workflow."""

import datetime as dt
import io

import pytest
import responses
from fake_target import FakeTarget
from PIL import Image, ImageDraw
from weather_factory import make_weather

# `countdown_core` must be imported before any display.* module, or it's a circular import.
import countdown_core  # noqa: F401, I001
import countdown_core.glow.energy_panel
from countdown_api.models import Screen
from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.display import DisplayController
from countdown_core.core.panel import Panel
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.glow.glow_client import GlowClient
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering.screen import DEFAULT_CANVAS
from countdown_core.spotify.models import Artist, Queue, TopResponse, Track
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_top_artists_panel import SpotifyTopArtistsPanel
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.spotify.spotify_top_tracks_panel import SpotifyTopTracksPanel
from countdown_core.system_screens.empty_panel import EmptyPanel
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_core.tfl.bus_arrival_panel import BusArrivalPanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_core.tfl.tfl_client import TflClient
from countdown_core.tfl.tube_arrival_panel import TubeArrivalPanel
from countdown_core.weather.weather_client import WeatherClient
from countdown_core.weather.weather_panel import WeatherPanel

_CONTROLLER = DisplayController(FakeTarget())
EVERYTHING = frozenset(ClientClasses)
NO_GLOWMARKT = EVERYTHING - {ClientClasses.GLOWMARKT}


LANDSCAPE = Screen(orientation="horizontal", width=800, height=480)


def _draw(panels: dict[str, Panel], available: frozenset[ClientClasses] = EVERYTHING) -> Image.Image:
    """The screen as it's drawn on an 800x480 landscape screen able to show `available`."""
    return _CONTROLLER.display_screen(panels, LANDSCAPE, available)


_FROZEN_NOW = dt.datetime(2026, 1, 15, 12, 0, 0)


class _FrozenDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    monkeypatch.setattr(countdown_core.glow.energy_panel, "datetime", _FrozenDatetime)


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

BUS_ARRIVALS = [
    BusArrival(naptanId="490000123W", lineName="N155", timeToStation=300, modeName="bus", destinationName="Somewhere"),
    BusArrival(naptanId="490000123W", lineName="P5", timeToStation=780, modeName="bus", destinationName="Peckham"),
]
TUBE_ARRIVALS = [
    TubeArrival(
        naptanId="940GZZLUKNG",
        lineName="Northern",
        timeToStation=120,
        modeName="tube",
        towards="Bank",
        destinationNaptanId=None,
    ),
    TubeArrival(
        naptanId="940GZZLUKNG",
        lineName="Northern",
        timeToStation=480,
        modeName="tube",
        towards="Morden",
        destinationNaptanId=None,
    ),
]

ENERGY = EnergyPanel(
    [
        {"start": (_FROZEN_NOW - dt.timedelta(hours=23 - hour)).isoformat(), "kwh": 0.4 + (hour % 6) * 0.25}
        for hour in range(24)
    ],
    0,
)


WEATHER = WeatherPanel(make_weather())

ALBUM_ART_URL = "https://example.com/album.jpg"


def _track(name: str, artist: str, album: str) -> dict:
    return {
        "name": name,
        "artists": [{"name": artist}],
        "album": {"name": album, "images": [{"width": 64, "height": 64, "url": ALBUM_ART_URL}]},
        "duration_ms": 427_000,
    }


SPOTIFY_QUEUE = Queue.model_validate(
    {
        "currently_playing": _track("Around The World", "Daft Punk", "Homework"),
        "queue": [_track("Da Funk", "Daft Punk", "Homework")],
    }
)
SPOTIFY_QUEUE_LAST_TRACK = SPOTIFY_QUEUE.model_copy(update={"queue": []})


def _album_art_bytes() -> bytes:
    """The album's only image, and a small one: thumbnail() never scales up, so it
    stays 64x64 in the 120px-high panel."""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _bus_and_tube_arrivals() -> CombinedArrivalPanel:
    return CombinedArrivalPanel(
        [
            BusArrivalPanel(BUS_STOP, BUS_ARRIVALS),
            TubeArrivalPanel(TUBE_STOP, TUBE_ARRIVALS),
        ]
    )


def _bus_only_arrivals() -> CombinedArrivalPanel:
    return CombinedArrivalPanel([BusArrivalPanel(BUS_STOP, BUS_ARRIVALS)])


def _four_stop_arrivals() -> CombinedArrivalPanel:
    """Two bus stops and two stations, for the layout with room for four."""
    other_bus_stop = BUS_STOP.model_copy(update={"common_name": "Walworth Road", "stop_letter": "E"})
    other_station = TUBE_STOP.model_copy(update={"common_name": "Oval Underground Station"})
    return CombinedArrivalPanel(
        [
            BusArrivalPanel(BUS_STOP, BUS_ARRIVALS),
            TubeArrivalPanel(TUBE_STOP, TUBE_ARRIVALS),
            BusArrivalPanel(other_bus_stop, BUS_ARRIVALS),
            TubeArrivalPanel(other_station, TUBE_ARRIVALS),
        ]
    )


SPOTIFY_NOT_CONFIGURED = SpotifyClient.message_panel("Not configured")

NOTICES = NoticeBoardPanel(
    [
        Notice("Met Office", Severity.SEVERE, "Red warning: wind until Fri 21:00"),
        Notice("Northern", Severity.WARNING, "Severe delays due to a signal failure at Camden Town."),
        Notice("Circle", Severity.INFO, "Issues Reported"),
        Notice("TfL", Severity.INFO, "Elephant & Castle: The lifts at the Northern line entrance are out of service"),
        Notice("Central", Severity.PLANNED, "Saturday 26 September, no service between Marble Arch and Loughton."),
    ]
)


def _panels(
    arrivals: Panel,
    energy: Panel = ENERGY,
    weather: Panel = WEATHER,
    spotify: Panel = SPOTIFY_NOT_CONFIGURED,
    notices: Panel = NOTICES,
) -> dict[str, Panel]:
    """What ApiRegistry.update_all() hands the layout: a panel
    for every API, a MessagePanel where one has nothing to show."""
    return {
        ClientClasses.TFL.api_name: arrivals,
        ClientClasses.GLOWMARKT.api_name: energy,
        ClientClasses.WEATHER.api_name: weather,
        ClientClasses.SPOTIFY.api_name: spotify,
        ClientClasses.NOTICE_BOARD.api_name: notices,
    }


def test_screen_with_no_stops_configured(snapshot):
    img = _draw(_panels(TflClient.message_panel("Needs configuring")))
    snapshot.assert_matches("screen_no_stops", img)


def test_screen_with_single_bus_stop(snapshot):
    img = _draw(_panels(_bus_only_arrivals()))
    snapshot.assert_matches("screen_single_bus_stop", img)


def test_screen_with_mixed_bus_and_tube(snapshot):
    img = _draw(_panels(_bus_and_tube_arrivals()))
    snapshot.assert_matches("screen_mixed_bus_and_tube", img)


def test_screen_without_weather_data_yet(snapshot):
    """The weather has never been fetched successfully: its area says so."""
    img = _draw(_panels(_bus_only_arrivals(), weather=WeatherClient.message_panel("Could not connect")))
    snapshot.assert_matches("screen_no_weather", img)


def test_screen_without_energy_panel(snapshot):
    """Glowmarkt not set up (the common case for a gifted device): no energy column, and
    the notices and Spotify take the full width instead."""
    img = _draw(_panels(_bus_only_arrivals(), energy=GlowClient.message_panel("Not configured")), NO_GLOWMARKT)
    snapshot.assert_matches("screen_no_energy", img)


@responses.activate
def test_screen_without_energy_panel_playing_spotify(snapshot):
    """Spotify at the full width it gets without the energy column."""
    responses.add(responses.GET, ALBUM_ART_URL, body=_album_art_bytes(), content_type="image/png")
    img = _draw(
        _panels(
            _bus_only_arrivals(),
            energy=GlowClient.message_panel("Not configured"),
            spotify=SpotifyPanel(SPOTIFY_QUEUE),
        ),
        NO_GLOWMARKT,
    )
    snapshot.assert_matches("screen_no_energy_spotify_playing", img)


def test_screen_with_no_notices(snapshot):
    """All clear: the notices are left out, and the stops take their room."""
    img = _draw(_panels(_bus_only_arrivals(), notices=NoticeBoardPanel([])))
    snapshot.assert_matches("screen_no_notices", img)


def test_screen_without_spotify(snapshot):
    """GlowLayout: the notices under the arrivals, the energy chart under the weather."""
    img = _draw(_panels(_bus_and_tube_arrivals()), EVERYTHING - {ClientClasses.SPOTIFY})
    snapshot.assert_matches("screen_no_spotify", img)


def test_screen_with_four_stops_and_neither_spotify_nor_energy(snapshot):
    """BaseLayout: four stops in a 2x2 grid beside the weather, the notices below."""
    img = _draw(_panels(_four_stop_arrivals()), EVERYTHING - {ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT})
    snapshot.assert_matches("screen_four_stops", img)


def test_empty_screen_stays_clear_of_the_screen_edges():
    img = EmptyPanel().render(*DEFAULT_CANVAS.size).convert("L")
    width, height = img.size

    for edge in [(0, 0, 40, height), (width - 40, 0, width, height)]:
        assert img.crop(edge).getextrema() == (255, 255)


def test_screen_with_no_panels_at_all_says_there_is_nothing_to_show(snapshot):
    """First cycle after boot, a paired device nobody's set up yet, or every API failing:
    a message saying so, not a blank white screen that looks broken (or a crash)."""
    img = _draw({})
    assert img.size == DEFAULT_CANVAS.size
    # Text-only and large: CI renders it ~1% differently from macOS.
    snapshot.assert_matches("screen_nothing_to_show", img, threshold=0.02)


@responses.activate
def test_screen_with_spotify_playing(snapshot):
    responses.add(responses.GET, ALBUM_ART_URL, body=_album_art_bytes(), content_type="image/png")
    img = _draw(_panels(_bus_only_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE)))
    snapshot.assert_matches("screen_spotify_playing", img)


@responses.activate
def test_screen_with_spotify_playing_the_last_queued_track(snapshot):
    """Nothing up next: that line is left out rather than drawn empty."""
    responses.add(responses.GET, ALBUM_ART_URL, body=_album_art_bytes(), content_type="image/png")
    img = _draw(_panels(_bus_only_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE_LAST_TRACK)))
    snapshot.assert_matches("screen_spotify_last_track", img)


def test_weather_panel_rainy_night(snapshot):
    """A different code and the night variant, at the shorter height the panel gets
    while a Spotify track is playing."""
    panel = WeatherPanel(
        make_weather(temperature=8.6, weather_code=63, is_day=False, high=11, low=6, precipitation_probability=85)
    )
    # Small and mostly text, so macOS/Linux anti-aliasing differences weigh more (CI: 0.0059).
    snapshot.assert_matches("weather_rainy_night", panel.render(528, 170), threshold=0.01)


def test_pairing_code_screen(snapshot):
    img = PairingCodePanel("C4FFEZ", "7218485b654f", has_changed=True).render(*DEFAULT_CANVAS.size)
    snapshot.assert_matches("screen_pairing_code", img, threshold=0.02)


@pytest.mark.parametrize("code", ["C4FFEZ", "ABCDEFGH", "ABCDEFGHJKLM"])
def test_pairing_code_stays_on_the_screen_whatever_its_length(code):
    img = PairingCodePanel(code, "7218485b654f", has_changed=True).render(*DEFAULT_CANVAS.size).convert("L")
    width, height = img.size

    for edge in [(0, 0, 30, height), (width - 30, 0, width, height)]:
        assert img.crop(edge).getextrema() == (255, 255)


def test_pairing_screen_without_a_code_draws_no_boxes():
    img = PairingCodePanel(None, "7218485b654f", has_changed=True).render(*DEFAULT_CANVAS.size)

    assert img.convert("L").crop((0, 232, 800, 342)).getextrema() == (255, 255)


PORTRAIT = (480, 800)


def test_pairing_code_screen_on_a_vertical_screen(snapshot):
    img = PairingCodePanel("C4FFEZ", "7218485b654f", has_changed=True).render(*PORTRAIT)
    snapshot.assert_matches("screen_pairing_code_portrait", img, threshold=0.02)


@pytest.mark.parametrize("code", ["C4FFEZ", "ABCDEFGH", "ABCDEFGHJKLM"])
def test_pairing_code_stays_on_a_vertical_screen_whatever_its_length(code):
    img = PairingCodePanel(code, "7218485b654f", has_changed=True).render(*PORTRAIT).convert("L")

    for edge in [(0, 0, 20, 800), (460, 0, 480, 800)]:
        assert img.crop(edge).getextrema() == (255, 255)


def test_screen_with_no_panels_at_all_on_a_vertical_screen(snapshot):
    img = _CONTROLLER.display_screen({}, Screen(orientation="vertical", width=800, height=480), EVERYTHING)
    assert img.size == PORTRAIT
    snapshot.assert_matches("screen_nothing_to_show_portrait", img, threshold=0.02)


def test_empty_screen_stays_clear_of_a_vertical_screens_edges():
    img = EmptyPanel().render(*PORTRAIT).convert("L")

    for edge in [(0, 0, 10, 800), (470, 0, 480, 800)]:
        assert img.crop(edge).getextrema() == (255, 255)


VERTICAL = Screen(orientation="vertical", width=800, height=480)


def _draw_portrait(panels: dict[str, Panel], available: frozenset[ClientClasses] = EVERYTHING) -> Image.Image:
    img = _CONTROLLER.display_screen(panels, VERTICAL, available)
    assert img.size == PORTRAIT
    return img


@responses.activate
def test_portrait_screen_playing_spotify(snapshot):
    """Top to bottom: the weather in full, two stops side by side, Spotify, the notices."""
    responses.add(responses.GET, ALBUM_ART_URL, body=_album_art_bytes(), content_type="image/png")
    img = _draw_portrait(_panels(_bus_and_tube_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE)))
    snapshot.assert_matches("screen_portrait_spotify", img)


def test_portrait_screen_without_spotify(snapshot):
    """Spotify's room goes to the stops (more departures each) and the notices."""
    img = _draw_portrait(_panels(_bus_and_tube_arrivals()), EVERYTHING - {ClientClasses.SPOTIFY})
    snapshot.assert_matches("screen_portrait_no_spotify", img)


def test_portrait_screen_with_no_stops_and_no_weather_yet(snapshot):
    """Messages take their slots' full width."""
    panels = _panels(
        TflClient.message_panel("Needs configuring"), weather=WeatherClient.message_panel("Could not connect")
    )
    img = _draw_portrait(panels, EVERYTHING - {ClientClasses.SPOTIFY})
    snapshot.assert_matches("screen_portrait_messages", img)


BUSY_BUS_ARRIVALS = [
    BusArrival(naptanId="490000123W", lineName=line, timeToStation=minutes * 60, modeName="bus", destinationName=to)
    for line, to, minutes in [
        ("36", "Queen's Park", 2),
        ("185", "Victoria", 4),
        ("36", "Queen's Park", 9),
        ("436", "Paddington", 11),
        ("N155", "Aldwych", 14),
        ("36", "Queen's Park", 16),
        ("P5", "Elephant & Castle", 18),
    ]
]
BUSY_TUBE_ARRIVALS = [
    TubeArrival(naptanId="940GZZLUKNG", lineName="Northern", timeToStation=minutes * 60, modeName="tube", towards=to)
    for to, minutes in [
        ("Morden", 1),
        ("Edgware via Bank", 3),
        ("Battersea Power Station", 5),
        ("High Barnet via Charing Cross", 7),
        ("Morden", 9),
        ("Edgware via Bank", 12),
    ]
]


def _styled_arrivals(style: str) -> CombinedArrivalPanel:
    return CombinedArrivalPanel(
        [BusArrivalPanel(BUS_STOP, BUSY_BUS_ARRIVALS, style), TubeArrivalPanel(TUBE_STOP, BUSY_TUBE_ARRIVALS, style)]
    )


@pytest.mark.parametrize("style", ["classic", "hero", "grouped", "card"])
def test_arrival_styles(snapshot, style):
    """Each look the config's tfl.style can pick, on a busy bus stop and station."""
    img = _draw(_panels(_styled_arrivals(style)))
    snapshot.assert_matches(f"screen_arrivals_{style}", img)


FEW_NOTICES = NoticeBoardPanel(NOTICES.notices[1:3])


def _many_route_arrivals() -> CombinedArrivalPanel:
    """More routes than the stops have room for, so the room the notices leave shows."""
    more_buses = [
        BusArrival(naptanId="490000123W", lineName=line, timeToStation=minutes * 60, modeName="bus", destinationName=to)
        for line, to, minutes in [("12", "Oxford Circus", 19), ("148", "White City", 21), ("171", "Holborn", 23)]
    ]
    more_trains = [
        TubeArrival(
            naptanId="940GZZLUKNG", lineName="Northern", timeToStation=minutes * 60, modeName="tube", towards=to
        )
        for to, minutes in [("Mill Hill East via Bank", 10), ("Kennington", 14), ("High Barnet via Bank", 17)]
    ]
    return CombinedArrivalPanel(
        [
            BusArrivalPanel(BUS_STOP, BUSY_BUS_ARRIVALS + more_buses),
            TubeArrivalPanel(TUBE_STOP, BUSY_TUBE_ARRIVALS + more_trains),
        ]
    )


@responses.activate
def test_screen_with_few_notices(snapshot):
    """The notices only as tall as their two rows, at the bottom: the stops get the rest."""
    responses.add(responses.GET, ALBUM_ART_URL, body=_album_art_bytes(), content_type="image/png")
    panels = _panels(_many_route_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE), notices=FEW_NOTICES)
    snapshot.assert_matches("screen_few_notices", _draw(panels))


@responses.activate
def test_screen_with_few_notices_and_no_energy_panel(snapshot):
    """Without the energy chart under it, the weather grows with the stops."""
    responses.add(responses.GET, ALBUM_ART_URL, body=_album_art_bytes(), content_type="image/png")
    panels = _panels(_many_route_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE), notices=FEW_NOTICES)
    snapshot.assert_matches("screen_few_notices_no_energy", _draw(panels, NO_GLOWMARKT))


@responses.activate
def test_portrait_screen_with_few_notices(snapshot):
    responses.add(responses.GET, ALBUM_ART_URL, body=_album_art_bytes(), content_type="image/png")
    panels = _panels(_many_route_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE), notices=FEW_NOTICES)
    snapshot.assert_matches("screen_portrait_few_notices", _draw_portrait(panels))


@pytest.mark.parametrize("style", ["hero", "grouped", "card"])
def test_arrival_styles_on_a_portrait_screen(snapshot, style):
    """Narrower stops, and taller ones without Spotify."""
    img = _draw_portrait(_panels(_styled_arrivals(style)), EVERYTHING - {ClientClasses.SPOTIFY})
    snapshot.assert_matches(f"screen_portrait_arrivals_{style}", img)


SPOTIFY_STYLES = ["classic", "airy", "hero", "card"]


def _cover_art_bytes() -> bytes:
    """A low-contrast gradient with a disc on it: the styles stretch its contrast, so the
    disc still shows once the screen dithers it."""
    img = Image.new("RGB", (300, 300))
    draw = ImageDraw.Draw(img)
    for y in range(300):
        draw.line((0, y, 300, y), fill=(150 - y // 5, 110, 90 + y // 6))
    draw.ellipse((70, 50, 230, 210), fill=(190, 170, 120))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _spotify_page(page: str, style: str) -> Panel:
    if page == "now":
        queue = SPOTIFY_QUEUE.model_copy(deep=True)
        queue.currently_playing.album.images[0].height = queue.currently_playing.album.images[0].width = 300
        return SpotifyPanel(queue, style)
    tracks = [
        _track("Pink + White", "Frank Ocean", "Blonde"),
        _track("Dreams", "Fleetwood Mac", "Rumours"),
        _track("Around The World", "Daft Punk", "Homework"),
        _track("Midnight City", "M83", "Hurry Up, We're Dreaming"),
        _track("Holocene", "Bon Iver", "Bon Iver, Bon Iver"),
    ]
    artists = ["Daft Punk", "Frank Ocean", "Fleetwood Mac", "Radiohead", "Little Simz"]
    return SpotifyTopPanel(
        "4 weeks",
        SpotifyTopTracksPanel(TopResponse[Track].model_validate({"items": tracks})),
        SpotifyTopArtistsPanel(TopResponse[Artist].model_validate({"items": [{"name": a} for a in artists]})),
        style,
    )


@responses.activate
@pytest.mark.parametrize("page", ["now", "top"])
@pytest.mark.parametrize("style", SPOTIFY_STYLES)
def test_spotify_styles(snapshot, style, page):
    """Each look the config's spotify.style can pick, on both of Spotify's pages, beside
    the energy chart."""
    responses.add(responses.GET, ALBUM_ART_URL, body=_cover_art_bytes(), content_type="image/png")
    img = _draw(_panels(_bus_and_tube_arrivals(), spotify=_spotify_page(page, style)))
    snapshot.assert_matches(f"screen_spotify_{style}_{page}", img)


@responses.activate
@pytest.mark.parametrize("page", ["now", "top"])
@pytest.mark.parametrize("style", SPOTIFY_STYLES[1:])
def test_spotify_styles_on_a_portrait_screen(snapshot, style, page):
    responses.add(responses.GET, ALBUM_ART_URL, body=_cover_art_bytes(), content_type="image/png")
    img = _draw_portrait(_panels(_bus_and_tube_arrivals(), spotify=_spotify_page(page, style)))
    snapshot.assert_matches(f"screen_portrait_spotify_{style}_{page}", img)
