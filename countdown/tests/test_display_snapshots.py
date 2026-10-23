"""Visual regression tests for the composed display screen.

Each scenario builds panels from representative data (no HTTP mocking -- see test_e2e_render_cycle.py) and
compares the render against a golden image in tests/images/. See the README for the workflow.
"""

import datetime as dt
import io
from typing import TYPE_CHECKING

import pytest
import qrcode
from PIL import Image, ImageChops, ImageDraw
from tfl_factory import tfl_panel
from weather_factory import forecast_json, make_weather

# `countdown_core` must be imported before any display.* module, or it's a circular import.
import countdown_core
import countdown_core.glow.energy_panel
from countdown_api.models import Screen
from countdown_core.core import moment
from countdown_core.core.api_registry import ClientClasses
from countdown_core.departures.departures_client import DeparturesClient
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.models import AiPlan, DeveloperSummary, GitHubSummary, Release, ReleaseJob
from countdown_core.entur.entur_arrival_panel import EnturArrivalPanel
from countdown_core.entur.entur_client import EnturDeparture, EnturStop, TransportMode
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.glow.glow_client import GlowClient
from countdown_core.i18n import set_locale
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.notices.notice_styles import STYLES as NOTICE_STYLES
from countdown_core.rendering import render, stops_fitting
from countdown_core.rendering.screen import DEFAULT_CANVAS
from countdown_core.spotify.models import Artist, Playback, Queue, Track
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_styles import APPLE_MUSIC_LABELS
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.system_screens.empty_panel import EmptyPanel
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_core.tfl.combined_arrival_panel import ROTATION_STRIP, CombinedArrivalPanel, Rotation
from countdown_core.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_core.weather.models import ForecastResponse, WeatherPeriod
from countdown_core.weather.weather_client import WeatherClient
from countdown_core.weather.weather_forecast import weather_pages
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel


def draw_screen(panels, screen, available, *, clock_format="24h") -> Image.Image:
    """Draw the screen as a render does (rendering.render), upright as the viewer sees it."""
    return render(panels, screen, available, clock_format=clock_format).image


# Every API but the developer panel, which goes in or above Spotify's area: its own tests add it.
EVERYTHING = frozenset(ClientClasses) - {ClientClasses.DEVELOPER}
NO_GLOWMARKT = EVERYTHING - {ClientClasses.GLOWMARKT}


LANDSCAPE = Screen(orientation="horizontal", width=800, height=480, format="mono-1bpp-packed")


def _draw(panels: dict[str, Panel], available: frozenset[ClientClasses] = EVERYTHING) -> Image.Image:
    """Draw the screen as it's drawn on an 800x480 landscape screen able to show `available`."""
    return draw_screen(panels, LANDSCAPE, available)


# Naive, as the panels' clock is: local wall-clock time.
_FROZEN_NOW = dt.datetime(2026, 1, 15, 12, 0, 0)  # noqa: DTZ001 -- see above


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
    """Make the album's only image, and a small one.

    thumbnail() never scales up, so it stays 64x64 in the 120px-high panel.
    """
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _bus_and_tube_arrivals() -> CombinedArrivalPanel:
    return CombinedArrivalPanel(
        [
            tfl_panel(BUS_STOP, BUS_ARRIVALS),
            tfl_panel(TUBE_STOP, TUBE_ARRIVALS),
        ]
    )


def _bus_only_arrivals() -> CombinedArrivalPanel:
    return CombinedArrivalPanel([tfl_panel(BUS_STOP, BUS_ARRIVALS)])


def _four_stop_arrivals() -> CombinedArrivalPanel:
    """Two bus stops and two stations, for the layout with room for four."""
    other_bus_stop = BUS_STOP.model_copy(update={"common_name": "Walworth Road", "stop_letter": "E"})
    other_station = TUBE_STOP.model_copy(update={"common_name": "Oval Underground Station"})
    return CombinedArrivalPanel(
        [
            tfl_panel(BUS_STOP, BUS_ARRIVALS),
            tfl_panel(TUBE_STOP, TUBE_ARRIVALS),
            tfl_panel(other_bus_stop, BUS_ARRIVALS),
            tfl_panel(other_station, TUBE_ARRIVALS),
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
    """Build what ApiRegistry.update_all() hands the layout.

    That's a panel for every API, a MessagePanel where one has nothing to show.
    """
    return {
        ClientClasses.TFL.api_name: arrivals,
        ClientClasses.GLOWMARKT.api_name: energy,
        ClientClasses.WEATHER.api_name: weather,
        ClientClasses.SPOTIFY.api_name: spotify,
        ClientClasses.NOTICE_BOARD.api_name: notices,
    }


def test_screen_with_no_stops_configured(snapshot):
    img = _draw(_panels(DeparturesClient.message_panel("Needs configuring")))
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
    """Glowmarkt not set up (the common case for a gifted device): no energy column.

    The notices and Spotify take the full width instead.
    """
    img = _draw(_panels(_bus_only_arrivals(), energy=GlowClient.message_panel("Not configured")), NO_GLOWMARKT)
    snapshot.assert_matches("screen_no_energy", img)


def test_screen_without_energy_panel_playing_spotify(snapshot):
    """Spotify at the full width it gets without the energy column."""
    img = _draw(
        _panels(
            _bus_only_arrivals(),
            energy=GlowClient.message_panel("Not configured"),
            spotify=SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes()),
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
    """First cycle after boot, a paired device nobody's set up yet, or every API failing.

    A message saying so, not a blank white screen that looks broken (or a crash).
    """
    img = _draw({})
    assert img.size == DEFAULT_CANVAS.size
    snapshot.assert_matches("screen_nothing_to_show", img)


def test_screen_with_spotify_playing(snapshot):
    img = _draw(_panels(_bus_only_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())))
    snapshot.assert_matches("screen_spotify_playing", img)


def test_screen_with_spotify_playing_the_last_queued_track(snapshot):
    """Nothing up next: that line is left out rather than drawn empty."""
    img = _draw(_panels(_bus_only_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE_LAST_TRACK, art=_album_art_bytes())))
    snapshot.assert_matches("screen_spotify_last_track", img)


def test_weather_panel_rainy_night(snapshot):
    """A different code and the night variant, at the size it's drawn beside two stops with the energy chart below."""
    panel = WeatherPanel(
        make_weather(temperature=8.6, weather_code=63, is_day=False, high=11, low=6, precipitation_probability=85)
    )
    snapshot.assert_matches("weather_rainy_night", panel.render(232, 185))


def test_pairing_code_screen(snapshot):
    img = PairingCodePanel("C4FFEZ", "7218485b654f").render(*DEFAULT_CANVAS.size)
    snapshot.assert_matches("screen_pairing_code", img)


@pytest.mark.parametrize("code", ["C4FFEZ", "ABCDEFGH", "ABCDEFGHJKLM"])
def test_pairing_code_stays_on_the_screen_whatever_its_length(code):
    img = PairingCodePanel(code, "7218485b654f").render(*DEFAULT_CANVAS.size).convert("L")
    width, height = img.size

    for edge in [(0, 0, 30, height), (width - 30, 0, width, height)]:
        assert img.crop(edge).getextrema() == (255, 255)


def test_pairing_qr_code_opens_the_pairing_page_with_the_code_filled_in(monkeypatch):
    urls = []
    add_data = qrcode.QRCode.add_data
    monkeypatch.setattr(
        qrcode.QRCode, "add_data", lambda self, data, *a, **k: (urls.append(data), add_data(self, data))[1]
    )

    PairingCodePanel("C4FFEZ", "7218485b654f").render(*DEFAULT_CANVAS.size)

    assert urls == ["https://auth.nikolaivorkinn.com/pair?code=C4FFEZ"]


PORTRAIT = (480, 800)


def test_pairing_code_screen_on_a_vertical_screen(snapshot):
    img = PairingCodePanel("C4FFEZ", "7218485b654f").render(*PORTRAIT)
    snapshot.assert_matches("screen_pairing_code_portrait", img)


@pytest.mark.parametrize("code", ["C4FFEZ", "ABCDEFGH", "ABCDEFGHJKLM"])
def test_pairing_code_stays_on_a_vertical_screen_whatever_its_length(code):
    img = PairingCodePanel(code, "7218485b654f").render(*PORTRAIT).convert("L")

    for edge in [(0, 0, 20, 800), (460, 0, 480, 800)]:
        assert img.crop(edge).getextrema() == (255, 255)


def test_screen_with_no_panels_at_all_on_a_vertical_screen(snapshot):
    img = draw_screen({}, Screen(orientation="vertical", width=800, height=480, format="mono-1bpp-packed"), EVERYTHING)
    assert img.size == PORTRAIT
    snapshot.assert_matches("screen_nothing_to_show_portrait", img)


def test_empty_screen_stays_clear_of_a_vertical_screens_edges():
    img = EmptyPanel().render(*PORTRAIT).convert("L")

    for edge in [(0, 0, 10, 800), (470, 0, 480, 800)]:
        assert img.crop(edge).getextrema() == (255, 255)


VERTICAL = Screen(orientation="vertical", width=800, height=480, format="mono-1bpp-packed")


def _draw_portrait(panels: dict[str, Panel], available: frozenset[ClientClasses] = NO_GLOWMARKT) -> Image.Image:
    """Draw the screen hung portrait, with the weather in full: the energy chart's tests add Glowmarkt."""
    img = draw_screen(panels, VERTICAL, available)
    assert img.size == PORTRAIT
    return img


def test_portrait_screen_playing_spotify(snapshot):
    """Top to bottom: the weather in full, two stops side by side, Spotify, the notices."""
    img = _draw_portrait(_panels(_bus_and_tube_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())))
    snapshot.assert_matches("screen_portrait_spotify", img)


def test_portrait_screen_without_spotify(snapshot):
    """Spotify's room goes to the stops (more departures each) and the notices."""
    img = _draw_portrait(_panels(_bus_and_tube_arrivals()), NO_GLOWMARKT - {ClientClasses.SPOTIFY})
    snapshot.assert_matches("screen_portrait_no_spotify", img)


def test_portrait_screen_with_no_notices(snapshot):
    """All clear: the notices are left out, and the stops take their room."""
    img = _draw_portrait(
        _panels(_bus_and_tube_arrivals(), notices=NoticeBoardPanel([])), NO_GLOWMARKT - {ClientClasses.SPOTIFY}
    )
    snapshot.assert_matches("screen_portrait_no_notices", img)


def test_portrait_screen_with_no_stops_and_no_weather_yet(snapshot):
    """Messages take their slots' full width."""
    panels = _panels(
        DeparturesClient.message_panel("Needs configuring"), weather=WeatherClient.message_panel("Could not connect")
    )
    img = _draw_portrait(panels, NO_GLOWMARKT - {ClientClasses.SPOTIFY})
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
        [tfl_panel(BUS_STOP, BUSY_BUS_ARRIVALS, style), tfl_panel(TUBE_STOP, BUSY_TUBE_ARRIVALS, style)]
    )


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_arrival_styles(snapshot, style):
    """Each look the config's style can pick, on a busy bus stop and station."""
    img = _draw(_panels(_styled_arrivals(style)))
    snapshot.assert_matches(f"screen_arrivals_{style}", img)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_arrival_styles_with_departures_due(snapshot, style):
    """A departure that's due says "due", with no "min" after it."""
    # The next 36 and 185, and the next Morden train.
    buses = [a.model_copy(update={"time_to_station": 0}) for a in BUSY_BUS_ARRIVALS[:2]] + BUSY_BUS_ARRIVALS[2:]
    trains = [BUSY_TUBE_ARRIVALS[0].model_copy(update={"time_to_station": 0}), *BUSY_TUBE_ARRIVALS[1:]]
    panel = CombinedArrivalPanel([tfl_panel(BUS_STOP, buses, style), tfl_panel(TUBE_STOP, trains, style)])
    snapshot.assert_matches(f"screen_arrivals_due_{style}", _draw(_panels(panel)))


FEW_NOTICES = NoticeBoardPanel(NOTICES.notices[1:3])


def _many_route_arrivals(style: str = "hero") -> CombinedArrivalPanel:
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
            tfl_panel(BUS_STOP, BUSY_BUS_ARRIVALS + more_buses, style),
            tfl_panel(TUBE_STOP, BUSY_TUBE_ARRIVALS + more_trains, style),
        ]
    )


def test_screen_with_few_notices(snapshot):
    """The notices only as tall as their two rows, at the bottom: the stops get the rest."""
    panels = _panels(
        _many_route_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes()), notices=FEW_NOTICES
    )
    snapshot.assert_matches("screen_few_notices", _draw(panels))


def test_screen_with_few_notices_and_no_energy_panel(snapshot):
    """Without the energy chart under it, the weather grows with the stops."""
    panels = _panels(
        _many_route_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes()), notices=FEW_NOTICES
    )
    snapshot.assert_matches("screen_few_notices_no_energy", _draw(panels, NO_GLOWMARKT))


def test_portrait_screen_with_few_notices(snapshot):
    panels = _panels(
        _many_route_arrivals(), spotify=SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes()), notices=FEW_NOTICES
    )
    snapshot.assert_matches("screen_portrait_few_notices", _draw_portrait(panels))


@pytest.mark.parametrize("style", list(NOTICE_STYLES))
def test_every_panel_in_one_style(snapshot, style):
    """Each look the config's style can pick, on every panel at once: the stops, Spotify and the notices."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, style, art=_album_art_bytes())
    notices = NoticeBoardPanel(NOTICES.notices, style)
    snapshot.assert_matches(
        f"screen_style_{style}", _draw(_panels(_many_route_arrivals(style), spotify=spotify, notices=notices))
    )


@pytest.mark.parametrize("style", list(NOTICE_STYLES))
def test_every_panel_in_one_style_on_a_portrait_screen(snapshot, style):
    spotify = SpotifyPanel(SPOTIFY_QUEUE, style, art=_album_art_bytes())
    notices = NoticeBoardPanel(FEW_NOTICES.notices, style)
    img = _draw_portrait(_panels(_many_route_arrivals(style), spotify=spotify, notices=notices))
    snapshot.assert_matches(f"screen_portrait_style_{style}", img)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_energy_chart_beside_the_weather_on_a_portrait_screen(snapshot, style):
    """With Glowmarkt the weather is the compact one, the energy chart beside it: the stops get the height it frees."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, style, art=_cover_art_bytes())
    notices = NoticeBoardPanel(FEW_NOTICES.notices, style)
    img = _draw_portrait(_panels(_many_route_arrivals(style), spotify=spotify, notices=notices), EVERYTHING)
    snapshot.assert_matches(f"screen_portrait_energy_{style}", img)


def test_energy_chart_beside_the_weather_on_a_portrait_screen_without_spotify(snapshot):
    img = _draw_portrait(_panels(_many_route_arrivals()), EVERYTHING - {ClientClasses.SPOTIFY})
    snapshot.assert_matches("screen_portrait_energy_no_spotify", img)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_arrival_styles_on_a_portrait_screen(snapshot, style):
    """Narrower stops, and taller ones without Spotify."""
    img = _draw_portrait(_panels(_styled_arrivals(style)), NO_GLOWMARKT - {ClientClasses.SPOTIFY})
    snapshot.assert_matches(f"screen_portrait_arrivals_{style}", img)


MAJORSTUEN = EnturStop(id="NSR:Quay:7333", name="Majorstuen", public_code="2", transport_modes=[TransportMode.METRO])
FROGNER_PLASS = EnturStop(id="NSR:StopPlace:58404", name="Frogner plass", transport_modes=[TransportMode.TRAM])


def _entur_departures(rows: list[tuple[str, str, int]], mode: TransportMode) -> list[EnturDeparture]:
    return [
        EnturDeparture(
            line=line,
            destination=to,
            mode=mode,
            expected_departure_time=dt.datetime(2026, 10, 16, 16, minutes, tzinfo=dt.UTC),
            time_to_station=minutes * 60,
        )
        for line, to, minutes in rows
    ]


BUSY_METRO_DEPARTURES = _entur_departures(
    [
        ("5", "Vestli", 1),
        ("3", "Mortensrud", 3),
        ("2", "Ellingsrudåsen", 4),
        ("5", "Vestli", 9),
        ("1", "Bergkrystallen", 6),
    ],
    TransportMode.METRO,
)
BUSY_TRAM_DEPARTURES = _entur_departures(
    [("12", "Majorstuen", 2), ("12", "Kværnerbyen", 5), ("12", "Majorstuen", 12), ("12", "Kværnerbyen", 15)],
    TransportMode.TRAM,
)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_oslo_stops_in_every_style(snapshot, style):
    """Entur's stops look like London's: a platform's code where a bus stop has its letter, and its mode's icon."""
    panel = CombinedArrivalPanel(
        [
            EnturArrivalPanel(MAJORSTUEN, BUSY_METRO_DEPARTURES, style),
            EnturArrivalPanel(FROGNER_PLASS, BUSY_TRAM_DEPARTURES, style),
        ]
    )
    snapshot.assert_matches(f"screen_oslo_arrivals_{style}", _draw(_panels(panel)))


MAJORSTUEN_STATION = EnturStop(
    id="NSR:StopPlace:59872", name="Majorstuen stasjon", transport_modes=[TransportMode.METRO, TransportMode.TRAM]
)
MIXED_DEPARTURES = [
    *_entur_departures([("5", "Vestli", 1), ("3", "Mortensrud", 3), ("1", "Bergkrystallen", 6)], TransportMode.METRO),
    *_entur_departures([("12", "Kværnerbyen", 2), ("19", "Ljabru", 5), ("12", "Majorstuen", 12)], TransportMode.TRAM),
]


def _mixed_stops(style: str) -> CombinedArrivalPanel:
    return CombinedArrivalPanel(
        [
            EnturArrivalPanel(MAJORSTUEN_STATION, MIXED_DEPARTURES, style),
            EnturArrivalPanel(FROGNER_PLASS, BUSY_TRAM_DEPARTURES, style),
        ]
    )


@pytest.mark.parametrize("style", ["airy", "hero"])
def test_a_stop_serving_several_modes_in_airy_and_hero(snapshot, style):
    """The header lists every mode and each row says which it is; a one-mode stop beside it looks as ever."""
    snapshot.assert_matches(f"screen_oslo_mixed_modes_{style}", _draw(_panels(_mixed_stops(style))))


@pytest.mark.parametrize("style", ["airy", "hero"])
def test_a_stop_serving_several_modes_on_a_portrait_screen(snapshot, style):
    img = _draw_portrait(_panels(_mixed_stops(style)), NO_GLOWMARKT - {ClientClasses.SPOTIFY})
    snapshot.assert_matches(f"screen_portrait_oslo_mixed_modes_{style}", img)


def test_oslo_screen_in_norwegian(snapshot):
    """The whole screen in Norwegian, with Norway's notices: Nikolai's translations, drawn."""
    set_locale("nb", "Europe/Oslo")
    try:
        panel = CombinedArrivalPanel(
            [
                EnturArrivalPanel(MAJORSTUEN, BUSY_METRO_DEPARTURES),
                EnturArrivalPanel(FROGNER_PLASS, BUSY_TRAM_DEPARTURES),
            ]
        )
        notices = NoticeBoardPanel(
            [
                Notice(
                    "MET",
                    Severity.WARNING,
                    "Oransje farevarsel: kraftig vind til fre.",
                    display_time=dt.datetime(2026, 10, 16, 21, tzinfo=dt.UTC),
                ),
                Notice("Linje 12", Severity.INFO, "Trikken kjører ikke mellom Majorstuen og Frogner plass."),
                Notice("Helligdag", Severity.REMINDER, "I morgen: Grunnlovsdag"),
            ]
        )
        img = _draw(_panels(panel, notices=notices))
    finally:
        set_locale("en", "Europe/London")
    snapshot.assert_matches("screen_oslo_norwegian", img)


def _rotating_arrivals() -> CombinedArrivalPanel:
    """Show the first of three pages of stops: one square filled, two empty."""
    return CombinedArrivalPanel(
        [
            tfl_panel(BUS_STOP, BUSY_BUS_ARRIVALS, "hero"),
            tfl_panel(TUBE_STOP, BUSY_TUBE_ARRIVALS, "hero"),
        ],
        Rotation(0, 3),
    )


def test_rotating_stops_show_which_are_on_screen(snapshot):
    """A square per page of stops under the arrivals, filled for the one shown now."""
    img = _draw(_panels(_rotating_arrivals()))
    snapshot.assert_matches("screen_arrivals_rotating", img)


def test_rotating_stops_on_a_portrait_screen(snapshot):
    img = _draw_portrait(_panels(_rotating_arrivals()), NO_GLOWMARKT - {ClientClasses.SPOTIFY})
    snapshot.assert_matches("screen_portrait_arrivals_rotating", img)


def test_rotation_squares_are_part_of_the_arrivals_panel():
    """The layouts make the arrivals' partial-refresh box from the panel's rendered size.

    So the squares, in the strip at the bottom of it, are repainted along with the stops.
    """
    rendered = _rotating_arrivals().render(800, 185)
    assert rendered.size[1] > 185 - ROTATION_STRIP
    assert ImageChops.difference(
        rendered.crop((0, 185 - ROTATION_STRIP, rendered.size[0], rendered.size[1])),
        Image.new("RGBA", (rendered.size[0], rendered.size[1] - 185 + ROTATION_STRIP), (255, 255, 255, 0)),
    ).getbbox()


SPOTIFY_STYLES = ["airy", "hero", "card"]


def _cover_art_bytes() -> bytes:
    """Make a low-contrast gradient with a disc on it.

    The styles stretch its contrast, so the disc still shows once the screen dithers it.
    """
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
        return SpotifyPanel(SPOTIFY_QUEUE, style, art=_cover_art_bytes())
    tracks = [
        _track("Pink + White", "Frank Ocean", "Blonde"),
        _track("Dreams", "Fleetwood Mac", "Rumours"),
        _track("Around The World", "Daft Punk", "Homework"),
        _track("Midnight City", "M83", "Hurry Up, We're Dreaming"),
        _track("Holocene", "Bon Iver", "Bon Iver, Bon Iver"),
    ]
    artists = ["Daft Punk", "Frank Ocean", "Fleetwood Mac", "Radiohead", "Little Simz"]
    return SpotifyTopPanel(
        "4 weeks", [Track.model_validate(t) for t in tracks], [Artist(name=a) for a in artists], style
    )


@pytest.mark.parametrize("page", ["now", "top"])
@pytest.mark.parametrize("style", SPOTIFY_STYLES)
def test_spotify_styles(snapshot, style, page):
    """Each look the config's style can pick, on both of Spotify's pages, beside the energy chart."""
    img = _draw(_panels(_bus_and_tube_arrivals(), spotify=_spotify_page(page, style)))
    snapshot.assert_matches(f"screen_spotify_{style}_{page}", img)


@pytest.mark.parametrize("page", ["now", "top"])
@pytest.mark.parametrize("style", SPOTIFY_STYLES)
def test_spotify_styles_on_a_portrait_screen(snapshot, style, page):
    img = _draw_portrait(_panels(_bus_and_tube_arrivals(), spotify=_spotify_page(page, style)))
    snapshot.assert_matches(f"screen_portrait_spotify_{style}_{page}", img)


@pytest.mark.parametrize("style", SPOTIFY_STYLES)
def test_apple_music_now(snapshot, style):
    """Apple Music's now page: the last played track, with the one before it, labelled for what they are."""
    panel = SpotifyPanel(SPOTIFY_QUEUE, style, APPLE_MUSIC_LABELS, _cover_art_bytes())
    img = _draw_portrait(_panels(_bus_and_tube_arrivals(), spotify=panel))
    snapshot.assert_matches(f"screen_portrait_apple_music_{style}_now", img)


def test_apple_music_now_on_a_landscape_screen(snapshot):
    """Its labels in the narrower area beside the energy chart."""
    panel = SpotifyPanel(SPOTIFY_QUEUE, "hero", APPLE_MUSIC_LABELS, _cover_art_bytes())
    snapshot.assert_matches("screen_apple_music_hero_now", _draw(_panels(_bus_and_tube_arrivals(), spotify=panel)))


@pytest.mark.parametrize("style", SPOTIFY_STYLES)
def test_apple_music_top_beside_spotify(snapshot, style):
    """With both services on, a top page says whose lists they are."""
    top = _spotify_page("top", style)
    top.service = "Apple Music"
    img = _draw_portrait(_panels(_bus_and_tube_arrivals(), spotify=top))
    snapshot.assert_matches(f"screen_portrait_apple_music_{style}_top", img)


@pytest.mark.parametrize("period", [WeatherPeriod.NOW, WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW])
def test_portrait_screen_in_12_hour_time(snapshot, period):
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    weather = WeatherPanel(next(page for page in pages if page.period is period))
    expiry = dt.datetime.fromisoformat("2026-01-15T12:07:00+00:00")
    notices = NoticeBoardPanel(
        [Notice("Met Office", Severity.WARNING, "Amber warning: wind until Thu", expiry, display_time=expiry)]
    )
    img = draw_screen(
        _panels(_bus_and_tube_arrivals(), weather=weather, notices=notices),
        Screen(orientation="vertical", width=800, height=480, format="mono-1bpp-packed"),
        NO_GLOWMARKT,  # the full weather, whose pages have the times
        clock_format="12h",
    )
    snapshot.assert_matches(f"screen_12h_portrait_{period.value}", img)


def test_landscape_screen_in_12_hour_time(snapshot):
    expiry = dt.datetime.fromisoformat("2026-01-15T00:07:00+00:00")
    notices = NoticeBoardPanel(
        [Notice("Met Office", Severity.WARNING, "Yellow warning: rain until Thu", expiry, display_time=expiry)]
    )
    img = draw_screen(_panels(_bus_and_tube_arrivals(), notices=notices), LANDSCAPE, EVERYTHING, clock_format="12h")
    snapshot.assert_matches("screen_12h_landscape", img)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_arrival_countdown_durations_do_not_use_the_clock_preference(style):
    panel = _styled_arrivals(style)

    assert panel.render(800, 185).tobytes() == panel.render(800, 185, clock_format="12h").tobytes()


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_clock_preference_does_not_leak_between_controllers_sharing_layouts_and_panels(orientation):
    panels = _panels(_bus_and_tube_arrivals())
    screen = Screen(orientation=orientation, width=800, height=480, format="mono-1bpp-packed")
    twelve_hour = draw_screen(panels, screen, EVERYTHING, clock_format="12h")
    default = draw_screen(panels, screen, EVERYTHING)
    explicit = draw_screen(panels, screen, EVERYTHING, clock_format="24h")

    assert default.tobytes() == explicit.tobytes()
    assert default.tobytes() != twelve_hour.tobytes()


DEVELOPER_SUMMARY = DeveloperSummary(
    github=GitHubSummary(login="nvorkinn", repos=9, open_prs=3, review_requests=1, failing=["auth-broker"]),
    plans=[
        AiPlan(name="Copilot", plan="Pro", renews=dt.date(2026, 11, 1), used=212, limit=300),
        AiPlan(name="Claude", plan="Max", renews=dt.date(2026, 11, 3)),
        AiPlan(name="Gemini", plan="AI Pro", renews=dt.date(2026, 11, 22)),
    ],
)
# The developer panel in Spotify's area, as it is on a screen without Spotify.
DEVELOPER_NOT_SPOTIFY = EVERYTHING - {ClientClasses.SPOTIFY} | {ClientClasses.DEVELOPER}
# And on portrait, with the weather in full.
DEVELOPER_PORTRAIT = DEVELOPER_NOT_SPOTIFY - {ClientClasses.GLOWMARKT}


def _developer_panels(panel: Panel, style: str) -> dict[str, Panel]:
    notices = NoticeBoardPanel(FEW_NOTICES.notices, style)
    return _panels(_many_route_arrivals(style), notices=notices) | {ClientClasses.DEVELOPER.api_name: panel}


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_developer_styles(snapshot, style):
    """Each look the config's style can pick, beside the energy chart: GitHub on the left, the AI plans right."""
    panels = _developer_panels(DeveloperPanel(DEVELOPER_SUMMARY, style), style)
    snapshot.assert_matches(f"screen_developer_{style}", _draw(panels, DEVELOPER_NOT_SPOTIFY))


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_developer_styles_on_a_portrait_screen(snapshot, style):
    panels = _developer_panels(DeveloperPanel(DEVELOPER_SUMMARY, style), style)
    snapshot.assert_matches(f"screen_portrait_developer_{style}", _draw_portrait(panels, DEVELOPER_PORTRAIT))


def test_developer_panel_without_the_energy_chart(snapshot):
    """Glowmarkt not set up: the developer panel in Spotify's area at the full width."""
    panels = _developer_panels(DeveloperPanel(DEVELOPER_SUMMARY), "hero")
    img = _draw(panels, DEVELOPER_NOT_SPOTIFY - {ClientClasses.GLOWMARKT})
    snapshot.assert_matches("screen_developer_no_energy", img)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_developer_panel_with_all_green_and_no_github_token(snapshot, style):
    """Every build passing on the full-width area, then no GitHub token yet: the plans still show."""
    green = DEVELOPER_SUMMARY.model_copy(
        update={
            "github": DEVELOPER_SUMMARY.github.model_copy(update={"open_prs": 0, "review_requests": 0, "failing": []})
        }
    )
    no_github = DEVELOPER_SUMMARY.model_copy(update={"github": None})
    img = Image.new("RGBA", (790, 250), "white")
    for i, summary in enumerate((green, no_github)):
        panel = DeveloperPanel(summary, style).render(790, 120)
        img.paste(panel, (0, i * 130), panel)
    snapshot.assert_matches(f"developer_{style}_green_and_no_github", img)


# The developer panel and Spotify both set up: the developer panel is a strip above Spotify.
DEVELOPER_AND_SPOTIFY = EVERYTHING | {ClientClasses.DEVELOPER}


def _above_spotify(panels: dict[str, Panel], style: str) -> dict[str, Panel]:
    return panels | {ClientClasses.SPOTIFY.api_name: SpotifyPanel(SPOTIFY_QUEUE, style, art=_album_art_bytes())}


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_developer_panel_above_spotify(snapshot, style):
    """A strip over Spotify's song: the pull requests and the builds, no plans."""
    panels = _above_spotify(_developer_panels(DeveloperPanel(DEVELOPER_SUMMARY, style), style), style)
    snapshot.assert_matches(f"screen_developer_above_spotify_{style}", _draw(panels, DEVELOPER_AND_SPOTIFY))


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_developer_panel_above_spotify_on_a_portrait_screen(snapshot, style):
    panels = _above_spotify(_developer_panels(DeveloperPanel(DEVELOPER_SUMMARY, style), style), style)
    img = _draw_portrait(panels, DEVELOPER_AND_SPOTIFY)
    snapshot.assert_matches(f"screen_portrait_developer_above_spotify_{style}", img)


def test_developer_panel_above_spotify_without_the_energy_chart(snapshot):
    """Glowmarkt not set up: the strip and Spotify both at the full width."""
    panels = _above_spotify(_developer_panels(DeveloperPanel(DEVELOPER_SUMMARY), "hero"), "hero")
    img = _draw(panels, DEVELOPER_AND_SPOTIFY - {ClientClasses.GLOWMARKT})
    snapshot.assert_matches("screen_developer_above_spotify_no_energy", img)


def test_developer_panel_above_spotify_beside_the_energy_chart_on_a_portrait_screen(snapshot):
    """Everything on portrait: the energy chart beside the compact weather, the strip over Spotify's song."""
    panels = _above_spotify(_developer_panels(DeveloperPanel(DEVELOPER_SUMMARY), "hero"), "hero")
    snapshot.assert_matches(
        "screen_portrait_energy_developer_above_spotify", _draw_portrait(panels, DEVELOPER_AND_SPOTIFY)
    )


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_compact_developer_panel_with_all_green_and_no_github_token(snapshot, style):
    """The strip at the landscape and portrait widths: failing, all green, then no GitHub token yet."""
    green = DEVELOPER_SUMMARY.model_copy(
        update={
            "github": DEVELOPER_SUMMARY.github.model_copy(update={"open_prs": 0, "review_requests": 0, "failing": []})
        }
    )
    no_github = DEVELOPER_SUMMARY.model_copy(update={"github": None})
    img = Image.new("RGBA", (536 + 20 + 470, 3 * 40 - 10), "white")
    for i, summary in enumerate((DEVELOPER_SUMMARY, green, no_github)):
        for x, width in ((0, 536), (556, 470)):
            panel = DeveloperPanel(summary, style).compacted()
            drawn = panel.render(width, panel.fitted_height(120))
            img.paste(drawn, (x, i * 40), drawn)
    snapshot.assert_matches(f"developer_compact_{style}", img)


RELEASE_NOW = dt.datetime(2026, 10, 17, 15, 4, tzinfo=dt.UTC)


def _job(key, state, needs=(), done=None, total=None):
    return ReleaseJob(key=key, name=key, state=state, needs=list(needs), done=done, total=total)


def _release(name, state, minutes, jobs, *, finished=None):
    """Make a release of `name` ("<repo> <title>") that started `minutes` before RELEASE_NOW."""
    repo, title = name.split()
    started = RELEASE_NOW - dt.timedelta(minutes=minutes)
    return Release(repo=repo, title=title, state=state, started=started, finished=finished, jobs=jobs)


# auth-broker's Release four minutes in: the images building, deploy-authentik waiting for approval.
AUTH_BROKER_RELEASE = _release(
    "auth-broker v1.27.0",
    "running",
    4,
    [
        _job("tag", "done"),
        _job("images", "running", ["tag"], 3, 5),
        _job("package", "done", ["tag"]),
        _job("deploy", "queued", ["tag", "images", "package"], 0, 2),
        _job("deploy-authentik", "approve", ["tag", "package"]),
        _job("terraform", "queued", ["tag", "deploy-authentik"]),
    ],
)
# pi-dashboard's: a chain of six, which wraps onto two rows.
PI_DASHBOARD_JOBS = [
    _job("verify-tag", "done"),
    _job("build-python", "done", ["verify-tag"]),
    _job("build-image", "running", ["build-python"], 4, 6),
    _job("publish-image", "queued", ["build-image"], 0, 3),
    _job("release", "queued", ["build-python", "publish-image"]),
    _job("deploy-renderers", "queued", ["release"]),
]
PI_DASHBOARD_RELEASE = _release("pi-dashboard v2.14.0", "running", 6, PI_DASHBOARD_JOBS)
PI_DASHBOARD_FAILED = _release(
    "pi-dashboard v2.14.0",
    "failed",
    5,
    [
        job.model_copy(update={"state": "failed", "done": 5}) if job.key == "build-image" else job
        for job in PI_DASHBOARD_JOBS
    ],
    finished=RELEASE_NOW,
)
AUTH_BROKER_RELEASED = AUTH_BROKER_RELEASE.model_copy(
    update={
        "state": "done",
        "started": RELEASE_NOW - dt.timedelta(minutes=12),
        "finished": RELEASE_NOW - dt.timedelta(minutes=3),
        "jobs": [job.model_copy(update={"state": "done", "done": job.total}) for job in AUTH_BROKER_RELEASE.jobs],
    }
)
# More jobs side by side than fit in a column: the last box says how many more.
CROWDED_RELEASE = _release(
    "countdown-api #12",
    "running",
    1,
    [_job("lint", "done")]
    + [_job(f"test-{n}", "running", ["lint"]) for n in range(5)]
    + [_job("publish", "skipped", ["test-0"])],
)


def _with_releases(*releases):
    return DEVELOPER_SUMMARY.model_copy(
        update={"github": DEVELOPER_SUMMARY.github.model_copy(update={"releases": list(releases)})}
    )


def _release_panels(style):
    panel = DeveloperPanel(_with_releases(AUTH_BROKER_RELEASE), style, now=RELEASE_NOW)
    notices = NoticeBoardPanel(
        [
            Notice("GitHub", Severity.REMINDER, "auth-broker v1.27.0: deploy-authentik waiting for your approval"),
            FEW_NOTICES.notices[0],
        ],
        style,
    )
    return _panels(_many_route_arrivals(style), notices=notices) | {ClientClasses.DEVELOPER.api_name: panel}


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_a_release_in_each_style(snapshot, style):
    """A running release in place of GitHub and the AI plans: its jobs as GitHub's graph, one waiting for approval."""
    snapshot.assert_matches(f"screen_developer_release_{style}", _draw(_release_panels(style), DEVELOPER_NOT_SPOTIFY))


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_a_release_on_a_portrait_screen(snapshot, style):
    img = _draw_portrait(_release_panels(style), DEVELOPER_PORTRAIT)
    snapshot.assert_matches(f"screen_portrait_developer_release_{style}", img)


def test_a_release_above_spotify(snapshot):
    """Its jobs in a row over Spotify's song, without the lines between them."""
    snapshot.assert_matches(
        "screen_developer_release_above_spotify",
        _draw(_above_spotify(_release_panels("hero"), "hero"), DEVELOPER_AND_SPOTIFY),
    )


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_a_release_in_the_strip_above_spotify(snapshot, style):
    """At the landscape and portrait widths: the jobs in a row without the lines, then too many for the row."""
    summaries = [
        _with_releases(PI_DASHBOARD_RELEASE, AUTH_BROKER_RELEASE),
        _with_releases(PI_DASHBOARD_FAILED),
        _with_releases(AUTH_BROKER_RELEASED),
        _with_releases(CROWDED_RELEASE),
    ]
    img = Image.new("RGBA", (536 + 20 + 470, len(summaries) * 62 - 10), "white")
    for i, summary in enumerate(summaries):
        for x, width in ((0, 536), (556, 470)):
            panel = DeveloperPanel(summary, style, now=RELEASE_NOW).compacted()
            drawn = panel.render(width, panel.fitted_height(120))
            img.paste(drawn, (x, i * 62), drawn)
    snapshot.assert_matches(f"developer_release_{style}_in_the_strip", img)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_a_release_as_it_goes(snapshot, style):
    """At the landscape and portrait widths: a long chain wrapped (with a second release), failed, released, crowded."""
    summaries = [
        _with_releases(PI_DASHBOARD_RELEASE, AUTH_BROKER_RELEASE),
        _with_releases(PI_DASHBOARD_FAILED),
        _with_releases(AUTH_BROKER_RELEASED),
        _with_releases(CROWDED_RELEASE),
    ]
    img = Image.new("RGBA", (536 + 20 + 470, len(summaries) * 130 - 10), "white")
    for i, summary in enumerate(summaries):
        for x, width in ((0, 536), (556, 470)):
            panel = DeveloperPanel(summary, style, now=RELEASE_NOW).render(width, 120)
            img.paste(panel, (x, i * 130), panel)
    snapshot.assert_matches(f"developer_release_{style}_as_it_goes", img)


# A 1872x1404 HD screen hung portrait: the portrait layout drawn at half the size and doubled, the stops taller.
HD_VERTICAL = Screen(orientation="vertical", width=1872, height=1404, format="png")


def _draw_hd_portrait(panels: dict[str, Panel], available: frozenset[ClientClasses] = NO_GLOWMARKT) -> Image.Image:
    img = draw_screen(panels, HD_VERTICAL, available)
    assert img.size == (1404, 1872)
    return img


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_every_panel_in_one_style_on_an_hd_portrait_screen(snapshot, style):
    """Top to bottom: the weather and two stops across the whole width, Spotify, the notices."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, style, art=_album_art_bytes())
    notices = NoticeBoardPanel(FEW_NOTICES.notices, style)
    img = _draw_hd_portrait(_panels(_many_route_arrivals(style), spotify=spotify, notices=notices))
    snapshot.assert_matches(f"screen_hd_portrait_style_{style}", img)


def test_energy_chart_beside_the_weather_on_an_hd_portrait_screen(snapshot):
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_cover_art_bytes())
    img = _draw_hd_portrait(_panels(_many_route_arrivals(), spotify=spotify, notices=FEW_NOTICES), EVERYTHING)
    snapshot.assert_matches("screen_hd_portrait_energy", img)


def test_hd_portrait_screen_without_spotify(snapshot):
    """Spotify's room goes to the stops and the notices."""
    img = _draw_hd_portrait(_panels(_many_route_arrivals()), NO_GLOWMARKT - {ClientClasses.SPOTIFY})
    snapshot.assert_matches("screen_hd_portrait_no_spotify", img)


def test_developer_panel_above_spotify_on_an_hd_portrait_screen(snapshot):
    panels = _above_spotify(_developer_panels(DeveloperPanel(DEVELOPER_SUMMARY), "hero"), "hero")
    img = _draw_hd_portrait(panels, DEVELOPER_AND_SPOTIFY - {ClientClasses.GLOWMARKT})
    snapshot.assert_matches("screen_hd_portrait_developer_above_spotify", img)


def test_spotify_top_tracks_on_an_hd_portrait_screen(snapshot):
    img = _draw_hd_portrait(_panels(_many_route_arrivals(), spotify=_spotify_page("top", "hero")))
    snapshot.assert_matches("screen_hd_portrait_spotify_top", img)


def test_spotify_queue_and_position_on_an_hd_portrait_screen(snapshot):
    """The wide now page with a full queue: the coming tracks listed, and a bar from the player's position."""
    now = dt.datetime(2026, 1, 15, 12, 0, tzinfo=dt.UTC)
    queue = Queue.model_validate(
        {
            "currently_playing": {**_track("Around The World", "Daft Punk", "Homework"), "id": "t1"},
            "queue": [
                _track("Da Funk", "Daft Punk", "Homework"),
                _track("Digital Love", "Daft Punk", "Discovery"),
                _track("Harder, Better, Faster, Stronger", "Daft Punk", "Discovery"),
                _track("One More Time", "Daft Punk", "Discovery"),
            ],
        }
    )
    playback = Playback(fetched_at=now, is_playing=True, progress_ms=168_000, track_id="t1")
    spotify = SpotifyPanel(queue, "hero", art=_cover_art_bytes(), playback=playback, now=now)
    img = _draw_hd_portrait(_panels(_many_route_arrivals(), spotify=spotify, notices=FEW_NOTICES))
    snapshot.assert_matches("screen_hd_portrait_spotify_queue", img)


@pytest.mark.parametrize("design", ["timeline", "overview", "poster"])
def test_each_other_weather_design_on_an_hd_portrait_screen(snapshot, design):
    """The config's weather.design (columns, the default, is in every other HD test), from a whole forecast.

    The overview draws every page, and the timeline the coming hours, which only a forecast has.
    """
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    weather = WeatherPanel(pages[0], design, pages)
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())
    img = _draw_hd_portrait(_panels(_many_route_arrivals(), weather=weather, spotify=spotify, notices=FEW_NOTICES))
    snapshot.assert_matches(f"screen_hd_portrait_weather_{design}", img)


# A 1872x1404 HD screen hung landscape: the black sidebar, and the stops, the hours and the rest in a column beside it.
HD_HORIZONTAL = Screen(orientation="horizontal", width=1872, height=1404, format="png")


def _draw_hd_landscape(panels: dict[str, Panel], available: frozenset[ClientClasses] = EVERYTHING) -> Image.Image:
    # The sidebar's clock is the render's moment.
    with moment.drawing_at(_FROZEN_NOW.replace(tzinfo=dt.UTC)):
        img = draw_screen(panels, HD_HORIZONTAL, available)
    assert img.size == (1872, 1404)
    return img


def _forecast_weather() -> WeatherPanel:
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    return WeatherPanel(pages[0], pages=pages)


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_every_panel_in_one_style_on_an_hd_landscape_screen(snapshot, style):
    """The sidebar with every page of a forecast; beside it the stops, the hours, energy and Spotify, the notices."""
    spotify = SpotifyPanel(SPOTIFY_QUEUE, style, art=_album_art_bytes())
    notices = NoticeBoardPanel(FEW_NOTICES.notices, style)
    panels = _panels(_many_route_arrivals(style), weather=_forecast_weather(), spotify=spotify, notices=notices)
    snapshot.assert_matches(f"screen_hd_landscape_style_{style}", _draw_hd_landscape(panels))


def test_spotify_wide_without_the_energy_chart_on_an_hd_landscape_screen(snapshot):
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_cover_art_bytes())
    panels = _panels(_many_route_arrivals(), weather=_forecast_weather(), spotify=spotify, notices=FEW_NOTICES)
    snapshot.assert_matches("screen_hd_landscape_no_energy", _draw_hd_landscape(panels, NO_GLOWMARKT))


def test_hd_landscape_screen_with_neither_spotify_nor_the_energy_chart(snapshot):
    """A lone weather page (no forecast's others, no hours): what comes next under it, and the stops get the rest."""
    available = NO_GLOWMARKT - {ClientClasses.SPOTIFY}
    snapshot.assert_matches(
        "screen_hd_landscape_neither", _draw_hd_landscape(_panels(_many_route_arrivals()), available)
    )


def test_developer_panel_above_spotify_on_an_hd_landscape_screen(snapshot):
    panels = _above_spotify(_developer_panels(DeveloperPanel(DEVELOPER_SUMMARY), "hero"), "hero")
    panels[ClientClasses.WEATHER.api_name] = _forecast_weather()
    img = _draw_hd_landscape(panels, DEVELOPER_AND_SPOTIFY)
    snapshot.assert_matches("screen_hd_landscape_developer_above_spotify", img)


def test_hd_landscape_screen_without_weather_data_yet(snapshot):
    """The sidebar keeps the clock and says why there's no weather."""
    panels = _panels(_many_route_arrivals(), weather=WeatherClient.message_panel("Could not connect"))
    snapshot.assert_matches("screen_hd_landscape_no_weather", _draw_hd_landscape(panels))


@pytest.mark.parametrize(
    ("layout", "minute"),
    [
        ("masthead", 0),
        ("column", 0),
        ("lockscreen", 0),
        ("timeline", 0),
        ("board", 0),
        ("pages", 0),
        ("pages", 1),
    ],
)
def test_each_other_layout_on_an_hd_portrait_screen(snapshot, rotation_clock, layout, minute):
    """The config's screen.layout, with everything to show; the pages layout's two pages a minute apart."""
    rotation_clock.at_minute(minute)
    spotify = SpotifyPanel(SPOTIFY_QUEUE, art=_album_art_bytes())
    panels = _panels(_many_route_arrivals(), weather=_forecast_weather(), spotify=spotify, notices=FEW_NOTICES)
    screen = HD_VERTICAL.model_copy(update={"layout": layout})
    with moment.drawing_at((_FROZEN_NOW + dt.timedelta(minutes=minute)).replace(tzinfo=dt.UTC)):
        img = draw_screen(panels, screen, EVERYTHING)
    assert img.size == (1404, 1872)
    name = f"{layout}_{'home' if minute else 'out'}" if layout == "pages" else layout
    snapshot.assert_matches(f"screen_hd_portrait_layout_{name}", img)


# --- Nothing playing: the music area folds away (ApiRegistry.available), and the stops may have a second row ------


def test_a_second_row_of_stops_beside_the_energy_chart_while_nothing_plays(snapshot):
    """The notices first, then the stops, two rows of them if there's room (Layout.stops_fitting)."""
    panels = _panels(_four_stop_arrivals(), notices=FEW_NOTICES)
    assert stops_fitting(panels, LANDSCAPE, EVERYTHING - {ClientClasses.SPOTIFY}) == 4

    snapshot.assert_matches("screen_second_row", _draw(panels, EVERYTHING - {ClientClasses.SPOTIFY}))


def test_a_second_row_of_stops_on_a_portrait_screen_while_nothing_plays(snapshot):
    panels = _panels(_four_stop_arrivals(), notices=FEW_NOTICES)
    available = NO_GLOWMARKT - {ClientClasses.SPOTIFY}
    assert stops_fitting(panels, VERTICAL, available) == 4

    snapshot.assert_matches("screen_portrait_second_row", _draw_portrait(panels, available))


def test_a_second_row_of_stops_on_an_hd_portrait_screen_while_nothing_plays(snapshot):
    panels = _panels(_four_stop_arrivals(), notices=FEW_NOTICES)
    available = NO_GLOWMARKT - {ClientClasses.SPOTIFY}
    assert stops_fitting(panels, HD_VERTICAL, available) == 4

    snapshot.assert_matches("screen_hd_portrait_second_row", _draw_hd_portrait(panels, available))


def test_a_second_row_of_stops_on_an_hd_landscape_screen_while_nothing_plays(snapshot):
    panels = _panels(_four_stop_arrivals(), weather=_forecast_weather(), notices=FEW_NOTICES)
    available = EVERYTHING - {ClientClasses.SPOTIFY}
    assert stops_fitting(panels, HD_HORIZONTAL, available) == 4

    snapshot.assert_matches("screen_hd_landscape_second_row", _draw_hd_landscape(panels, available))
