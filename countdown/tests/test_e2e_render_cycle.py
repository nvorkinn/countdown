"""End-to-end tests for the display refresh cycle, through to a rendered image.

DisplayLoop.run() calls the render (render.render), which builds the real clients afresh each time, reading a fake
store (conftest's `store`) that the tests fill as the providers would, with any other HTTP (the broker, the notices)
mocked with `responses`.

The loop's sleep is a fake that raises after N calls (see _run_cycles()). Most tests leave the broker's /config
unmocked, so refreshing the config fails and is swallowed, and the loop carries on with the config it was built with.
"""

import asyncio
import datetime
import io
import json
import logging
import socket
from typing import TYPE_CHECKING

import pytest
import responses
from config_factory import broker_payload, make_config
from fake_target import FakeTarget
from PIL import Image, ImageDraw, ImageFont
from redis.exceptions import ConnectionError as RedisConnectionError
from test_utils import put_departures
from weather_factory import forecast_json

from countdown_api.models import ConfigResponse, Screen
from countdown_core import i18n
from countdown_core import render as render_module
from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.core import display_loop
from countdown_core.core.api_registry import API_NAMES
from countdown_core.core.display import DisplayController
from countdown_core.core.display_loop import DisplayLoop
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering import layout_for
from countdown_core.rendering.layouts.classic import BaseLayout, GlowLayout
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.store import keys
from countdown_core.store.location import StoredLocation
from countdown_core.store.tfl_notices import StoredLineStatus, StoredStationDisruptions
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.tfl.models import Postcode
from countdown_core.utils import utils
from countdown_core.weather.weather_panel import WeatherPanel
from countdown_credentials.registration import RendererRegistration

if TYPE_CHECKING:
    from collections.abc import Callable

# Material Symbols stood in for (conftest), so every frame draws offline too: these are about what's drawn when.
pytestmark = pytest.mark.usefixtures("symbols_font")

TEST_BROKER_URL = "https://broker.example.com"
DEVICE_ID = "test-device"


def _app_config_json(pairing_code: str | None = None, interval: int = 1) -> dict:
    """Build what the broker's /api/config returns: all of it, as the contract says (see config_factory)."""
    return broker_payload(interval=interval, pairing_code=pairing_code)


BUS_STOP = "490000123W"
TUBE_STATION = "940GZZLUKNG"


TRACK_JSON = {
    "name": "Test Song",
    "artists": [{"name": "Test Artist"}],
    "album": {"name": "Test Album", "images": [{"width": 64, "height": 64, "url": "https://example.com/album.jpg"}]},
    "duration_ms": 200_000,
}


class _StopLoopError(Exception):
    """Raised by the fake sleep to break out of DisplayLoop.run()'s `while True`."""


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(buf, format="PNG")
    return buf.getvalue()


def _put_stops(store, *stop_ids: str) -> None:
    """Store each stop's departures as the TfL provider would: a bus stop's, or a Tube station's (940...)."""
    for stop_id in stop_ids:
        if stop_id.startswith("940"):
            put_departures(
                store, stop_id, [("Northern", "Bank", 120)], name="Kennington Underground Station", mode="tube"
            )
        else:
            put_departures(store, stop_id, [("N155", "Somewhere", 300)], stop_letter="W")


def _put_energy(store) -> None:
    """Store the device's readings as auth-broker's glow-provider would: two hourly ones, at UTC (offset 0)."""
    now = datetime.datetime.now(datetime.UTC)
    hourly = {
        "data": [
            [(now - datetime.timedelta(hours=4)).timestamp(), 1.5],
            [(now - datetime.timedelta(hours=6)).timestamp(), 2.25],
        ]
    }
    stored = {"fetched_at": now.isoformat(), "device_id": DEVICE_ID, "offset": "0", "resource_id": "elec-id"}
    empty = {"data": []}
    store.set(keys.energy(DEVICE_ID), json.dumps(stored | {"hourly": hourly, "daily": empty, "monthly": empty}))


def _put_stops_and_energy(store, *stop_ids: str) -> None:
    _put_stops(store, *stop_ids)
    _put_energy(store)


def _put_music(store, queue: dict, top: dict | None = None) -> None:
    """Store Spotify's player (and top lists, the same for both periods) as auth-broker's music provider would."""
    fetched_at = datetime.datetime.now(datetime.UTC).isoformat()
    store.set(keys.spotify(DEVICE_ID), json.dumps({"fetched_at": fetched_at, "spotify": queue}))
    lists = {"long_term": top, "short_term": top} if top is not None else None
    store.set(keys.spotify_top(DEVICE_ID), json.dumps({"fetched_at": fetched_at, "spotify": lists}))


def _put_forecast(store, forecast: dict, location: str = "London") -> None:
    """Store `location`'s forecast as the weather provider would."""
    stored = {"fetched_at": datetime.datetime.now(datetime.UTC).isoformat(), "location": location, "forecast": forecast}
    store.set(keys.weather(location), json.dumps(stored))


@pytest.fixture
def reads(store, monkeypatch) -> list[str]:
    """Record the key of every read of the store."""
    seen: list[str] = []
    get = store.get

    def recording_get(key):
        seen.append(key)
        return get(key)

    monkeypatch.setattr(store, "get", recording_get)
    return seen


def _make_config() -> ConfigResponse:
    # Glowmarkt is off unless the broker switches it on. Passed in, not set afterwards: it's
    # checked when the config is validated.
    config = make_config(glowmarkt={"enabled": True})
    config.interval = 1  # the fake sleep in _run_cycles() means this never actually waits
    return config


def _build_loop(config: ConfigResponse, monkeypatch, pairing_code: str | None = None) -> DisplayLoop:
    broker = BrokerClient(RendererRegistration(TEST_BROKER_URL, "test-secret", DEVICE_ID))
    asyncio.run(broker.initialise())
    if pairing_code is not None:
        config = config.model_copy(update={"pairing_code": pairing_code})
    return DisplayLoop(broker, config, DisplayController(FakeTarget()), OtlpPublisher(API_NAMES))


def _spy_on_frames(monkeypatch, layouts: list[type] | None = None) -> list[dict]:
    """Record the panels dict of every frame drawn (and its layout, in `layouts`).

    The frame is still drawn and shown for real, so the whole render path is exercised too.
    """
    seen: list[dict] = []
    original = render_module.draw

    def spy(panels, screen, available, *, clock_format="24h", now=None):
        seen.append(dict(panels))
        if layouts is not None:
            layouts.append(type(layout_for(screen, available)[0]))
        return original(panels, screen, available, clock_format=clock_format, now=now)

    monkeypatch.setattr(render_module, "draw", spy)
    return seen


def _run_cycles(loop: DisplayLoop, monkeypatch, cycles: int = 1) -> None:
    """Run exactly `cycles` refresh cycles.

    The loop sleeps once after each, and the fake sleep stops it there (before the broker config refresh, on the
    last one).
    """
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= cycles:
            raise _StopLoopError

    loop._sleep = fake_sleep
    with pytest.raises(_StopLoopError):
        asyncio.run(loop.run())


def _calls_to(url_prefix: str) -> int:
    return sum(1 for call in responses.calls if call.request.url.startswith(url_prefix))


def _put_cold_forecast(store, config: ConfigResponse) -> None:
    """Configure the weather for London, and store a freezing forecast for it."""
    config.weather.location = "London"
    _put_forecast(
        store,
        forecast_json(
            current={"temperature_2m": -20.0, "weather_code": 45, "is_day": 0},
            daily={"temperature_2m_max": [-7.8] + [-8.0] * 7, "temperature_2m_min": [-20.1] + [-21.0] * 7}
            | {"precipitation_probability_max": [None] * 8},
        ),
    )


@responses.activate
def test_full_render_cycle_without_spotify_track(isolated_cwd, monkeypatch, store):
    config = _make_config()
    config.tfl.stop_ids = [BUS_STOP, TUBE_STATION]
    _put_stops_and_energy(store, BUS_STOP, TUBE_STATION)
    _put_cold_forecast(store, config)
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert len(shown) == 1
    panels = shown[0]
    assert isinstance(panels["tfl"], CombinedArrivalPanel)
    assert len(panels["tfl"].arrival_panels) == 2
    assert isinstance(panels["glowmarkt"], EnergyPanel)
    assert panels["glowmarkt"].page_index == 0
    assert [reading["kwh"] for reading in panels["glowmarkt"].readings] == [2.25, 1.5]  # oldest first
    assert panels["spotify"].message == "Not configured"  # not enabled, so never even built
    assert isinstance(panels["weather"], WeatherPanel)
    assert panels["weather"].weather.temperature == -20.0


@responses.activate
def test_full_render_cycle_skips_glowmarkt_when_switched_off(isolated_cwd, monkeypatch, store, reads):
    """The common case: Glowmarkt never set up, so its readings are never even read."""
    config = make_config()
    config.interval = 1
    config.tfl.stop_ids = [BUS_STOP]
    assert config.glowmarkt.enabled is False
    _put_stops_and_energy(store, BUS_STOP)
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert keys.energy(DEVICE_ID) not in reads
    assert "glowmarkt" not in loop.last.statuses  # never built while switched off
    assert shown[0]["glowmarkt"].message == "Not configured"
    assert len(shown[0]["tfl"].arrival_panels) == 1


@responses.activate
def test_full_render_cycle_with_spotify_track(isolated_cwd, monkeypatch, store):
    config = _make_config()
    config.tfl.stop_ids = [BUS_STOP]
    config.spotify.enabled = True
    _put_stops_and_energy(store, BUS_STOP)
    _put_music(store, {"currently_playing": TRACK_JSON, "queue": []})
    # Its cover, as the music provider stores it: the image's bytes, by its URL.
    store.set(keys.album_art("https://example.com/album.jpg"), _png_bytes())
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert isinstance(shown[0]["spotify"], SpotifyPanel)
    assert shown[0]["spotify"].queue.currently_playing.name == "Test Song"
    assert shown[0]["spotify"].art == _png_bytes()  # the screen was rendered with the cover from the store
    assert _calls_to("https://example.com/album.jpg") == 0  # not from its image host


@responses.activate
def test_full_render_cycle_when_nothing_is_playing(isolated_cwd, monkeypatch, store):
    config = _make_config()
    config.tfl.stop_ids = [BUS_STOP]
    config.spotify.enabled = True
    _put_stops_and_energy(store, BUS_STOP)
    top = {"tracks": {"items": []}, "artists": {"items": [{"name": "An Artist"}]}}
    _put_music(store, {"currently_playing": None, "queue": []}, top)
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert isinstance(shown[0]["spotify"], SpotifyTopPanel)  # the player has nothing to show
    assert isinstance(shown[0]["glowmarkt"], EnergyPanel)


@responses.activate
def test_full_render_cycle_with_weather(isolated_cwd, monkeypatch, store):
    config = _make_config()
    config.weather.location = "London"
    _put_forecast(store, forecast_json())
    _put_energy(store)
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert isinstance(shown[0]["weather"], WeatherPanel)
    assert shown[0]["weather"].weather.temperature == 14.2


@responses.activate
def test_full_render_cycle_reads_the_weather_under_the_brokers_place_key(isolated_cwd, monkeypatch, store, reads):
    """The broker sends the picked place's key beside its name, and subscribes the key: that's what's read."""
    config = _make_config()
    config.weather.location, config.weather.key = "London", "london-gb-2643743"
    _put_forecast(store, forecast_json(), location="london-gb-2643743")
    _put_energy(store)
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert shown[0]["weather"].weather.temperature == 14.2
    assert keys.weather("london-gb-2643743") in reads
    assert keys.weather("London") not in reads


@responses.activate
def test_one_provider_being_down_does_not_stop_the_others_reaching_the_screen(isolated_cwd, monkeypatch, store):
    """The TfL provider is down, so its stop's key has expired; the energy readings are still there."""
    config = _make_config()
    config.tfl.stop_ids = [BUS_STOP]
    _put_energy(store)
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert len(shown) == 1
    assert shown[0]["tfl"].message == "Not available"  # no stop's departures in the store
    assert isinstance(shown[0]["glowmarkt"], EnergyPanel)


@responses.activate
def test_every_render_builds_its_clients_afresh_and_reads_the_store_again(isolated_cwd, monkeypatch, store, reads):
    """Nothing is kept from one render to the next: each reads what it draws from the store."""
    config = _make_config()
    stops = [BUS_STOP, TUBE_STATION, "490000456X", "940GZZLUABC"]
    config.tfl.stop_ids = stops
    _put_stops_and_energy(store, *stops)
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert len(shown) == 2
    assert reads.count(keys.energy(DEVICE_ID)) == 2
    assert shown[1]["glowmarkt"] is not shown[0]["glowmarkt"]
    assert shown[1]["tfl"] is not shown[0]["tfl"]


@responses.activate
def test_run_shows_pairing_screen_and_skips_normal_display_while_unpaired(isolated_cwd, monkeypatch):
    """While a pairing code is active, run() shouldn't fetch anything or draw the dashboard.

    Nothing is mocked, so any request would fail.
    """
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert loop.display.target.paints == [None]  # the pairing screen, in full
    assert shown == []
    assert loop.last is None
    assert len(responses.calls) == 0


@responses.activate
def test_run_only_repaints_pairing_screen_when_code_changes(isolated_cwd, monkeypatch):
    """An unchanged pairing code isn't repainted.

    /config is mocked here (always the same code), so each cycle really re-reads it from the broker.
    """
    responses.add(
        responses.GET,
        f"{TEST_BROKER_URL}/api/config",
        json=_app_config_json(pairing_code="ABC123"),
    )
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")

    _run_cycles(loop, monkeypatch, cycles=2)

    assert loop.display.target.paints == [None]
    assert loop.display.target.renewals == 1


@responses.activate
def test_refresh_broker_config_applies_the_new_config_and_pairing_state(isolated_cwd, monkeypatch):
    responses.add(
        responses.GET,
        f"{TEST_BROKER_URL}/api/config",
        json=_app_config_json(pairing_code=None, interval=42),
    )
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")

    asyncio.run(loop.refresh_broker_config())

    assert loop.config.interval == 42
    assert loop.config.pairing_code is None  # the device just got paired


@responses.activate
def test_config_changes_from_the_broker_reach_the_screen_on_the_next_cycle(isolated_cwd, monkeypatch, store):
    """The device boots with nothing set up; the owner then adds a stop on the broker.

    Cycle 2 must pick that up without a restart.
    """
    _put_stops(store, BUS_STOP)
    new_config = _app_config_json()
    new_config["tfl"]["stop_ids"] = [BUS_STOP]
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/config", json=new_config)
    loop = _build_loop(make_config(), monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert shown[0]["tfl"].message == "Needs configuring"
    assert len(shown[1]["tfl"].arrival_panels) == 1


@responses.activate
@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_clock_changes_reformat_the_panels_on_the_next_render(orientation, monkeypatch, store, reads):
    config = _make_config()
    config.screen.orientation = orientation
    config.tfl.stop_ids = [BUS_STOP]
    _put_stops_and_energy(store, BUS_STOP)
    _put_cold_forecast(store, config)
    for clock_format in ("12h", "24h"):
        responses.add(
            responses.GET,
            f"{TEST_BROKER_URL}/api/config",
            json=config.model_dump(mode="json") | {"clock_format": clock_format},
        )
    expiry = datetime.datetime.fromisoformat("2026-09-25T21:00:00+01:00")
    notice_panel = NoticeBoardPanel(
        [
            Notice("Met Office", Severity.WARNING, "Wind until Fri", expiry, display_time=expiry),
            Notice("TfL", Severity.INFO, "Works at 18:00"),
        ]
    )
    monkeypatch.setattr(NoticeBoardClient, "_update", lambda self: notice_panel)
    loop = _build_loop(config, monkeypatch)
    # The same moment for each render, as if each came at once (as a nudge's does): only the clock format differs.
    stopped = datetime.datetime.now(datetime.UTC)
    loop._clock = lambda: stopped
    monkeypatch.setattr(display_loop, "next_render", lambda now, *, paced: now)
    texts, frames = [], []
    original_text = ImageDraw.ImageDraw.text
    original_render = render_module.draw

    def record_text(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    def record_frame(panels, screen, available, *, clock_format="24h", now=None):
        texts.clear()
        frame = original_render(panels, screen, available, clock_format=clock_format, now=now)
        frames.append((dict(panels), clock_format, frame, set(texts)))
        return frame

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(render_module, "draw", record_frame)
    _run_cycles(loop, monkeypatch, cycles=3)

    assert [f[1] for f in frames] == ["24h", "12h", "24h"]
    first, twelve_hour, restored = frames
    assert first[2].image.tobytes() == restored[2].image.tobytes()
    assert first[2].image.tobytes() != twelve_hour[2].image.tobytes()
    assert first[2].volatile == twelve_hour[2].volatile == restored[2].volatile
    for frame in frames:
        assert "Works at 18:00" in frame[3]
    assert any(text.endswith("21:00") for text in first[3])
    assert any(text.endswith("9:00 PM") for text in twelve_hour[3])
    if orientation == "vertical":
        assert {"07:58", "16:22"} <= first[3]
        assert {"7:58 AM", "4:22 PM"} <= twelve_hour[3]
    else:
        assert any(text.endswith((" AM", " PM")) and "until" not in text for text in twelve_hour[3])
    # Each render reads what it draws afresh.
    assert reads.count(keys.weather("London")) == 3
    assert reads.count(keys.energy(DEVICE_ID)) == 3
    assert reads.count(keys.departures(BUS_STOP)) == 3
    assert loop.display.target.paints == [None, None, None]


@responses.activate
def test_the_layout_follows_what_the_config_lets_the_device_show(isolated_cwd, monkeypatch, store):
    """Boots with Glowmarkt set up but not Spotify (GlowLayout), then loses Glowmarkt.

    The owner removes Glowmarkt's credentials on the broker, and the next cycle drops the energy column (BaseLayout,
    with neither). The screen is hung landscape: those are its layouts.
    """
    _put_energy(store)
    landscape = {"orientation": "horizontal", "width": 800, "height": 480}
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/config", json=broker_payload(interval=1, screen=landscape))
    config = _make_config()
    config.screen = Screen.model_validate(landscape)
    loop = _build_loop(config, monkeypatch)
    layouts: list[type] = []
    _spy_on_frames(monkeypatch, layouts)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert layouts == [GlowLayout, BaseLayout]


@responses.activate
def test_the_first_cycle_reports_how_long_startup_took_once(isolated_cwd, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    responses.add(
        responses.GET,
        f"{TEST_BROKER_URL}/api/config",
        json=_app_config_json(pairing_code="ABC123"),
    )
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")

    _run_cycles(loop, monkeypatch, cycles=2)

    assert caplog.text.count("First cycle finished") == 1


def _mock_config(**kwargs) -> None:
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/config", json=_app_config_json(**kwargs))


@responses.activate
@pytest.mark.parametrize(("pairing_code", "stage"), [("ABC123", "pairing"), (None, "running")])
def test_run_reports_the_stage_it_is_in(isolated_cwd, monkeypatch, pairing_code, stage):
    _mock_config(pairing_code=pairing_code)
    loop = _build_loop(make_config(), monkeypatch, pairing_code=pairing_code)

    _run_cycles(loop, monkeypatch)

    assert loop.publisher.device_status.stage == stage


def _record_publishes(loop: DisplayLoop, monkeypatch) -> list[str]:
    """Record the stage each time the loop asks the publisher to publish early."""
    stages: list[str] = []
    monkeypatch.setattr(loop.publisher, "publish_soon", lambda: stages.append(loop.publisher.device_status.stage))
    return stages


@responses.activate
def test_run_asks_for_an_early_publish_only_when_the_stage_changes(isolated_cwd, monkeypatch):
    _mock_config(pairing_code="ABC123")
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")
    published = _record_publishes(loop, monkeypatch)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert published == ["pairing"]  # waiting_for_broker -> pairing, then no change


@responses.activate
def test_run_forces_a_publish_when_pairing_completes(isolated_cwd, monkeypatch):
    _mock_config(pairing_code=None)
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")
    published = _record_publishes(loop, monkeypatch)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert published == ["pairing", "running"]


@responses.activate
def test_refreshing_the_config_records_when_the_broker_last_answered(isolated_cwd, monkeypatch):
    _mock_config()
    loop = _build_loop(make_config(), monkeypatch)
    assert loop.publisher.device_status.last_broker_sync is None

    _run_cycles(loop, monkeypatch, cycles=2)

    assert loop.publisher.device_status.last_broker_sync is not None


@responses.activate
@pytest.mark.parametrize("pairing_code", ["ABC123", None], ids=["pairing", "dashboard"])
def test_run_paints_a_screen_the_panel_could_not_take_on_the_next_cycle(isolated_cwd, monkeypatch, pairing_code):
    _mock_config(pairing_code=pairing_code)
    loop = _build_loop(make_config(), monkeypatch, pairing_code=pairing_code)
    target = loop.display.target
    target.accepts = False

    def the_screen_appears() -> None:
        target.accepts = True

    _run_cycles_between(loop, monkeypatch, the_screen_appears)

    assert target.paints == [None]  # the same picture, painted once there was a screen to take it


@pytest.fixture
def no_network(monkeypatch) -> list[tuple]:
    """Record, and refuse, every attempt to reach the network at all, below `responses` (which mocks only requests).

    A client that swallows the error can't hide it: the test checks the list. The one download the renderer makes,
    Material Symbols (a font, fetched from GitHub once and kept on disk), is stood in for by a font it ships with.
    """
    attempts: list[tuple] = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        msg = "the network was reached"
        raise OSError(msg)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(
        utils, "_cached_font", lambda url, filename: ImageFont.truetype(utils.FONTS_DIR / "Ubuntu-Regular.ttf")
    )
    utils.google_symbols.cache_clear()
    yield attempts
    utils.google_symbols.cache_clear()  # don't leave the stand-in cached for other tests


SOUTHWARK = Postcode(
    postcode="SE17 3LL",
    latitude=51.48873,
    longitude=-0.09766,
    country="England",
    region="London",
    admin_district="Southwark",
)
OSLO = Postcode(postcode="0150", latitude=59.9116, longitude=10.7505, country="Norway", admin_district="Oslo")


@responses.activate
@pytest.mark.parametrize(("place", "country"), [(SOUTHWARK, "GB"), (OSLO, "NO")], ids=["uk", "norway"])
def test_a_full_render_cycle_reaches_nothing_but_the_broker(monkeypatch, store, no_network, place, country):
    """Every panel and the notice board, its postcode placed too, read from the store: no upstream is asked anything."""
    config = _make_config()
    config.tfl.stop_ids = [BUS_STOP, TUBE_STATION]
    config.spotify.enabled = True
    config.notice_board.postcode, config.notice_board.country = place.postcode, country
    _put_stops_and_energy(store, BUS_STOP, TUBE_STATION)
    _put_cold_forecast(store, config)
    _put_music(store, {"currently_playing": TRACK_JSON, "queue": []})
    store.set(keys.album_art("https://example.com/album.jpg"), _png_bytes())
    now = datetime.datetime.now(datetime.UTC)
    location = StoredLocation(fetched_at=now, postcode=place.postcode, found=True, location=place)
    store.set(keys.location(place.postcode), location.model_dump_json())  # as provider-locations placed it
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/config", json=config.model_dump(mode="json"))
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)
    placed = []
    ensure_location = NoticeBoardClient._ensure_location

    def recording(board, now):
        ensure_location(board, now)
        placed.append(board.location)

    monkeypatch.setattr(NoticeBoardClient, "_ensure_location", recording)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert no_network == []
    assert [call.request.url for call in responses.calls if not call.request.url.startswith(TEST_BROKER_URL)] == []
    assert len(shown) == 2
    assert placed == [place, place]  # each render's board placed from the store, not by asking anyone
    assert isinstance(shown[0]["tfl"], CombinedArrivalPanel)
    assert isinstance(shown[0]["weather"], WeatherPanel)
    assert isinstance(shown[0]["spotify"], SpotifyPanel)


# --- Expiry: the provider's TTL is all there is -----------------------------------------------------------------------


def _run_cycles_between(loop: DisplayLoop, monkeypatch, *between: Callable[[], None]) -> None:
    """Run a cycle, then one more after each of `between`, which runs while the loop sleeps (a provider's doing)."""
    steps = list(between)

    async def fake_sleep(seconds: float) -> None:
        if not steps:
            raise _StopLoopError
        steps.pop(0)()

    loop._sleep = fake_sleep
    with pytest.raises(_StopLoopError):
        asyncio.run(loop.run())


@responses.activate
def test_an_expired_key_draws_not_available_in_the_screens_language_and_redis_down_keeps_the_frame(
    monkeypatch, store, no_network
):
    """The whole render path: the loop, the render, the real clients reading the store, the layout and the drawing.

    The screen is in Norwegian. Its departures' key expires between the first and second frames (the TfL provider is
    down), while the energy readings stay: the second frame draws "Ikke tilgjengelig" where the departures were,
    translated as it's drawn, and none of the old departures. Then Redis stops answering: the third frame keeps
    what the second drew (the render draws the last render's panel for a client that fails), rather than saying
    anything has expired.
    """
    monkeypatch.setattr(i18n, "_language", i18n.language())  # put the language back afterwards
    monkeypatch.setattr(i18n, "_timezone", i18n.local_zone())
    config = _make_config()
    config.language, config.timezone = "nb", "Europe/Oslo"
    config.tfl.stop_ids = [BUS_STOP]
    _put_stops_and_energy(store, BUS_STOP)
    now = datetime.datetime.now(datetime.UTC)
    # The stop's disruptions, as the TfL notices provider keeps them on their own TTLs: all clear.
    store.set(
        keys.tfl_line_status(BUS_STOP), StoredLineStatus(fetched_at=now, stop_id=BUS_STOP, lines=[]).model_dump_json()
    )
    stations = StoredStationDisruptions(fetched_at=now, stop_id=BUS_STOP, disruptions=[])
    store.set(keys.tfl_stations(BUS_STOP), stations.model_dump_json())
    loop = _build_loop(config, monkeypatch)
    frames: list[tuple[dict, set[str], bytes]] = []
    texts: list[str] = []
    original_text = ImageDraw.ImageDraw.text
    original_render = render_module.draw

    def record_text(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    def record_frame(panels, screen, available, *, clock_format="24h", now=None):
        texts.clear()
        frame = original_render(panels, screen, available, clock_format=clock_format, now=now)
        frames.append((dict(panels), set(texts), frame.image.tobytes()))
        return frame

    def redis_down() -> None:
        def unreachable(*args, **kwargs):
            raise RedisConnectionError

        monkeypatch.setattr(store, "get", unreachable)
        monkeypatch.setattr(store, "mget", unreachable)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(render_module, "draw", record_frame)

    _run_cycles_between(loop, monkeypatch, lambda: store.delete(keys.departures(BUS_STOP)), redis_down)

    assert len(frames) == 3
    (first, first_texts, _), (expired, expired_texts, expired_image), (kept, _, kept_image) = frames
    assert isinstance(first["tfl"], CombinedArrivalPanel)
    assert "N155" in first_texts
    assert expired["tfl"].message == "Not available"
    # The board has the stop's line status, and Norway's holidays (on Norwegian time with no postcode), worked out
    # here: so the departures' is the only panel that can say it.
    assert isinstance(expired["notice_board"], NoticeBoardPanel)
    assert "Ikke tilgjengelig" in expired_texts  # drawn, in the screen's language
    assert "Not available" not in expired_texts
    assert "N155" not in expired_texts  # no old departures
    assert isinstance(expired["glowmarkt"], EnergyPanel)  # its key is still there: drawn as before
    # Redis down: every panel read from the store is the one the last frame drew, and so is the picture.
    assert all(kept[name] is expired[name] for name in ("tfl", "glowmarkt", "notice_board"))
    assert kept_image == expired_image
