"""End-to-end tests for the display refresh cycle: the real clients and ApiRegistry inside
DisplayLoop.run(), against HTTP mocked with `responses`, through to a rendered image.

`asyncio.sleep` is replaced by a fake that raises after N calls (see _run_cycles()). Most
tests leave the broker's /config unmocked, so refreshing the config fails and is swallowed,
and the loop carries on with the config it was built with.
"""

import asyncio
import datetime
import io
import logging
from types import SimpleNamespace

import pytest
import responses
from config_factory import broker_payload, make_config
from fake_target import FakeTarget
from PIL import Image, ImageDraw
from weather_factory import forecast_json

from countdown_api.models import ConfigResponse, Screen
from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.core import display as display_module
from countdown_core.core import display_loop
from countdown_core.core.display import DisplayController
from countdown_core.core.display_loop import DisplayLoop
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering import layout_for
from countdown_core.rendering.layouts.classic import BaseLayout, GlowLayout
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.weather.weather_panel import WeatherPanel
from countdown_credentials.registration import RendererRegistration

TEST_BROKER_URL = "https://broker.example.com"
GLOWMARKT_URL = "https://api.glowmarkt.com/api/v0-1"
QUEUE_URL = f"{TEST_BROKER_URL}/api/spotify/queue"


def _app_config_json(pairing_code: str | None = None, interval: int = 1) -> dict:
    """What the broker's /api/config returns: all of it, as the contract says (see config_factory)."""
    return broker_payload(interval=interval, pairing_code=pairing_code)


BUS_STOP_JSON = {
    "naptanId": "490000123W",
    "commonName": "Elephant & Castle",
    "modes": ["bus"],
    "additionalProperties": [],
    "children": [],
    "stopType": "NaptanPublicBusCoachTram",
    "stopLetter": "W",
}
METRO_STOP_JSON = {
    "naptanId": "940GZZLUKNG",
    "commonName": "Kennington Underground Station",
    "modes": ["tube"],
    "additionalProperties": [],
    "children": [],
    "stopType": "NaptanMetroStation",
}
BUS_ARRIVALS_JSON = [
    {
        "naptanId": "490000123W",
        "lineName": "N155",
        "timeToStation": 300,
        "modeName": "bus",
        "destinationName": "Somewhere",
    }
]
METRO_ARRIVALS_JSON = [
    {
        "naptanId": "940GZZLUKNG",
        "lineName": "Northern",
        "timeToStation": 120,
        "modeName": "tube",
        "towards": "Bank",
        "destinationNaptanId": None,
    }
]
TRACK_JSON = {
    "name": "Test Song",
    "artists": [{"name": "Test Artist"}],
    "album": {"name": "Test Album", "images": [{"width": 64, "height": 64, "url": "https://example.com/album.jpg"}]},
    "duration_ms": 200_000,
}


class _StopLoop(Exception):
    """Raised by the fake sleep to break out of DisplayLoop.run()'s `while True`."""


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(buf, format="PNG")
    return buf.getvalue()


def _mock_tfl(stop_json_by_id: dict, arrivals_json_by_id: dict) -> None:
    for stop_id, stop_json in stop_json_by_id.items():
        responses.add(responses.GET, f"https://api.tfl.gov.uk/StopPoint/{stop_id}", json=stop_json)
    for stop_id, arrivals_json in arrivals_json_by_id.items():
        responses.add(responses.GET, f"https://api.tfl.gov.uk/StopPoint/{stop_id}/Arrivals", json=arrivals_json)


def _mock_glowmarkt() -> None:
    responses.add(responses.POST, f"{GLOWMARKT_URL}/auth", json={"token": "fake-token"})
    responses.add(
        responses.GET,
        f"{GLOWMARKT_URL}/virtualentity",
        json=[{"resources": [{"name": "electricity consumption", "resourceId": "elec-id"}]}],
    )

    now = datetime.datetime.now().astimezone()
    four_hours_earlier = now - datetime.timedelta(hours=4)
    six_hours_earlier = now - datetime.timedelta(hours=6)
    responses.add(
        responses.GET,
        f"{GLOWMARKT_URL}/resource/elec-id/readings",
        json={"data": [[four_hours_earlier.timestamp(), 1.5], [six_hours_earlier.timestamp(), 2.25]]},
    )


def _mock_tfl_and_glowmarkt(stop_json_by_id: dict, arrivals_json_by_id: dict) -> None:
    _mock_tfl(stop_json_by_id, arrivals_json_by_id)
    _mock_glowmarkt()


def _make_config() -> ConfigResponse:
    # Without credentials Glowmarkt is off. Passed in, not set afterwards: they're
    # checked when the config is validated.
    config = make_config(glowmarkt={"username": "dummy@example.com", "password": "dummy-password"})
    config.interval = 1  # the fake sleep in _run_cycles() means this never actually waits
    return config


def _build_loop(config: ConfigResponse, monkeypatch, pairing_code: str | None = None) -> DisplayLoop:
    broker = BrokerClient(RendererRegistration(TEST_BROKER_URL, "test-secret", "test-device"))
    asyncio.run(broker.initialise())
    # Via get_pairing_code_panel, to seed the broker's cache like fetch_app_config() does.
    pairing_code_panel = broker.get_pairing_code_panel(ConfigResponse.model_validate(_app_config_json(pairing_code)))
    return DisplayLoop(broker, config, pairing_code_panel, DisplayController(FakeTarget()))


def _spy_on_frames(monkeypatch, layouts: list[type] | None = None) -> list[dict]:
    """Records the panels dict of every frame drawn (and its layout, in `layouts`). The frame
    is still drawn and shown for real, so the whole render path is exercised too.
    """
    seen: list[dict] = []
    original = display_module.render

    def spy(panels, screen, available, *, clock_format="24h"):
        seen.append(dict(panels))
        if layouts is not None:
            layouts.append(type(layout_for(screen, available)[0]))
        return original(panels, screen, available, clock_format=clock_format)

    monkeypatch.setattr(display_module, "render", spy)
    return seen


def _run_cycles(loop: DisplayLoop, monkeypatch, cycles: int = 1) -> None:
    """Runs exactly `cycles` refresh cycles: the loop sleeps once after each, and the
    fake sleep stops it there (before the broker config refresh, on the last one).
    """
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= cycles:
            raise _StopLoop

    monkeypatch.setattr(display_loop, "asyncio", SimpleNamespace(sleep=fake_sleep))
    with pytest.raises(_StopLoop):
        asyncio.run(loop.run())


def _calls_to(url_prefix: str) -> int:
    return sum(1 for call in responses.calls if call.request.url.startswith(url_prefix))


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _mock_forecast() -> None:
    responses.add(
        responses.GET,
        FORECAST_URL,
        json=forecast_json(
            current={"temperature_2m": -20.0, "weather_code": 45, "is_day": 0},
            daily={"temperature_2m_max": [-7.8] + [-8.0] * 7, "temperature_2m_min": [-20.1] + [-21.0] * 7}
            | {"precipitation_probability_max": [None] * 8},
        ),
    )


@responses.activate
def test_full_render_cycle_without_spotify_track(isolated_cwd, monkeypatch):
    config = _make_config()
    config.tfl.stop_ids = ["490000123W", "940GZZLUKNG"]
    _mock_tfl_and_glowmarkt(
        {"490000123W": BUS_STOP_JSON, "940GZZLUKNG": METRO_STOP_JSON},
        {"490000123W": BUS_ARRIVALS_JSON, "940GZZLUKNG": METRO_ARRIVALS_JSON},
    )
    _mock_forecast()
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
    assert isinstance(panels["weather"], WeatherPanel)  # no location: the weather at the North Pole
    forecast_request = next(c.request for c in responses.calls if c.request.url.startswith(FORECAST_URL))
    assert "latitude=90.0" in forecast_request.url


@responses.activate
def test_full_render_cycle_skips_glowmarkt_when_credentials_empty(isolated_cwd, monkeypatch):
    """The common case: Glowmarkt never set up. No glowmarkt.com endpoint is mocked, so
    any call to it would fail and show in the call count below.
    """
    config = make_config()
    config.interval = 1
    config.tfl.stop_ids = ["490000123W"]
    assert config.glowmarkt.username is None and config.glowmarkt.password is None
    _mock_tfl({"490000123W": BUS_STOP_JSON}, {"490000123W": BUS_ARRIVALS_JSON})
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert _calls_to("https://api.glowmarkt.com") == 0
    assert "glowmarkt" not in loop.api_reg.clients  # never built without credentials
    assert shown[0]["glowmarkt"].message == "Not configured"
    assert len(shown[0]["tfl"].arrival_panels) == 1


@responses.activate
def test_full_render_cycle_with_spotify_track(isolated_cwd, monkeypatch):
    config = _make_config()
    config.tfl.stop_ids = ["490000123W"]
    config.spotify.enabled = True
    _mock_tfl_and_glowmarkt({"490000123W": BUS_STOP_JSON}, {"490000123W": BUS_ARRIVALS_JSON})
    responses.add(responses.GET, QUEUE_URL, json={"currently_playing": TRACK_JSON, "queue": []})
    responses.add(responses.GET, "https://example.com/album.jpg", body=_png_bytes(), content_type="image/png")
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert isinstance(shown[0]["spotify"], SpotifyPanel)
    assert shown[0]["spotify"].queue.currently_playing.name == "Test Song"
    assert _calls_to("https://example.com/album.jpg") == 1  # the screen really was rendered with the art


@responses.activate
def test_full_render_cycle_when_nothing_is_playing(isolated_cwd, monkeypatch):
    config = _make_config()
    config.tfl.stop_ids = ["490000123W"]
    config.spotify.enabled = True
    _mock_tfl_and_glowmarkt({"490000123W": BUS_STOP_JSON}, {"490000123W": BUS_ARRIVALS_JSON})
    responses.add(responses.GET, QUEUE_URL, json={"currently_playing": None, "queue": []})
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/spotify/top/tracks", json={"items": []})
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/spotify/top/artists", json={"items": []})
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert isinstance(shown[0]["spotify"], SpotifyTopPanel)  # the player has nothing to show
    assert isinstance(shown[0]["glowmarkt"], EnergyPanel)


@responses.activate
def test_full_render_cycle_with_weather(isolated_cwd, monkeypatch):
    config = _make_config()
    config.weather.location = "London"
    responses.add(
        responses.GET,
        "https://geocoding-api.open-meteo.com/v1/search",
        json={"results": [{"name": "London", "latitude": 51.5, "longitude": -0.12}]},
    )
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/forecast",
        json=forecast_json(),
    )
    _mock_glowmarkt()
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert isinstance(shown[0]["weather"], WeatherPanel)
    assert shown[0]["weather"].weather.temperature == 14.2


@responses.activate
def test_one_api_being_down_does_not_stop_the_others_reaching_the_screen(isolated_cwd, monkeypatch):
    config = _make_config()
    config.tfl.stop_ids = ["490000123W"]
    responses.add(responses.GET, "https://api.tfl.gov.uk/StopPoint/490000123W", status=500)
    _mock_glowmarkt()
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch)

    assert len(shown) == 1
    assert shown[0]["tfl"].message == "Could not connect"  # no stops could be resolved
    assert isinstance(shown[0]["glowmarkt"], EnergyPanel)


@responses.activate
def test_later_cycles_reuse_client_state_and_keep_slow_panels_on_screen(isolated_cwd, monkeypatch):
    """Clients live for the whole run: on the second cycle Glowmarkt and TfL aren't due
    yet, so they aren't called again, but their last panels are still drawn.
    """
    config = _make_config()
    config.tfl.stop_ids = ["490000123W", "940GZZLUKNG", "490000456X", "940GZZLUABC"]
    _mock_tfl_and_glowmarkt(
        {
            "490000123W": BUS_STOP_JSON,
            "940GZZLUKNG": METRO_STOP_JSON,
            "490000456X": dict(BUS_STOP_JSON, naptanId="490000456X"),
            "940GZZLUABC": dict(METRO_STOP_JSON, naptanId="940GZZLUABC"),
        },
        {
            "490000123W": BUS_ARRIVALS_JSON,
            "940GZZLUKNG": METRO_ARRIVALS_JSON,
            "490000456X": BUS_ARRIVALS_JSON,
            "940GZZLUABC": METRO_ARRIVALS_JSON,
        },
    )
    loop = _build_loop(config, monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert len(shown) == 2
    assert _calls_to(f"{GLOWMARKT_URL}/auth") == 1
    assert _calls_to(f"{GLOWMARKT_URL}/virtualentity") == 1
    assert _calls_to(f"{GLOWMARKT_URL}/resource") == 1  # not due again yet
    assert shown[1]["glowmarkt"] is shown[0]["glowmarkt"]
    assert shown[1]["tfl"] is shown[0]["tfl"]


@responses.activate
def test_run_shows_pairing_screen_and_skips_normal_display_while_unpaired(isolated_cwd, monkeypatch):
    """While a pairing code is active, run() shouldn't fetch anything or paint the
    normal screen (nothing is mocked, so any request would fail).
    """
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")
    shown = _spy_on_frames(monkeypatch)
    pairing_screens = []
    monkeypatch.setattr(
        loop.display,
        "display_pairing_screen",
        lambda panel, screen: pairing_screens.append((panel.pairing_code, panel.device_id)),
    )

    _run_cycles(loop, monkeypatch)

    assert pairing_screens == [("ABC123", "test-device")]
    assert shown == []
    assert loop.api_reg.panels == {}
    assert len(responses.calls) == 0


@responses.activate
def test_run_only_repaints_pairing_screen_when_code_changes(isolated_cwd, monkeypatch):
    """An unchanged pairing code isn't repainted. /config is mocked here (always the same
    code), so each cycle really re-derives the panel through BrokerClient's cache.
    """
    responses.add(
        responses.GET,
        f"{TEST_BROKER_URL}/api/config",
        json=_app_config_json(pairing_code="ABC123"),
    )
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")
    screens_shown = []
    monkeypatch.setattr(
        loop.display, "display_pairing_screen", lambda panel, screen: screens_shown.append(panel.pairing_code)
    )

    # cycle 1: pairing_code_panel seeded by _build_loop, has_changed=True -> repaint
    # cycle 2: refresh_broker_config() re-fetched the same code -> has_changed=False -> no repaint
    _run_cycles(loop, monkeypatch, cycles=2)

    assert screens_shown == ["ABC123"]


@responses.activate
def test_refresh_broker_config_applies_the_new_interval_and_pairing_state(isolated_cwd, monkeypatch):
    responses.add(
        responses.GET,
        f"{TEST_BROKER_URL}/api/config",
        json=_app_config_json(pairing_code=None, interval=42),
    )
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")

    asyncio.run(loop.refresh_broker_config())

    assert loop.interval == 42
    assert loop.config.interval == 42
    assert loop.pairing_code_panel.pairing_code is None  # the device just got paired
    assert loop.pairing_code_panel.has_changed is True


@responses.activate
def test_config_changes_from_the_broker_reach_the_screen_on_the_next_cycle(isolated_cwd, monkeypatch):
    """The device boots with nothing set up; the owner then adds a stop on the broker.
    Cycle 2 must pick that up without a restart.
    """
    _mock_tfl({"490000123W": BUS_STOP_JSON}, {"490000123W": BUS_ARRIVALS_JSON})
    new_config = _app_config_json()
    new_config["tfl"]["stop_ids"] = ["490000123W"]
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/config", json=new_config)
    loop = _build_loop(make_config(), monkeypatch)
    shown = _spy_on_frames(monkeypatch)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert shown[0]["tfl"].message == "Needs configuring"
    assert len(shown[1]["tfl"].arrival_panels) == 1


@responses.activate
@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_clock_changes_reformat_cached_panels_without_rebuilding_or_polling(orientation, monkeypatch):
    config = _make_config()
    config.screen.orientation = orientation
    config.tfl.stop_ids = ["490000123W"]
    _mock_tfl_and_glowmarkt({"490000123W": BUS_STOP_JSON}, {"490000123W": BUS_ARRIVALS_JSON})
    _mock_forecast()
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
    texts, frames = [], []
    original_text = ImageDraw.ImageDraw.text
    original_render = display_module.render

    def record_text(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    def record_frame(panels, screen, available, *, clock_format="24h"):
        texts.clear()
        frame = original_render(panels, screen, available, clock_format=clock_format)
        frames.append(
            (
                dict(panels),
                clock_format,
                frame,
                set(texts),
                dict(loop.api_reg.clients),
                {name: c.last_updated for name, c in loop.api_reg.clients.items()},
            )
        )
        return frame

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(display_module, "render", record_frame)
    _run_cycles(loop, monkeypatch, cycles=3)

    assert [f[1] for f in frames] == ["24h", "12h", "24h"]
    first, twelve_hour, restored = frames
    assert first[2].image.tobytes() == restored[2].image.tobytes()
    assert first[2].image.tobytes() != twelve_hour[2].image.tobytes()
    assert first[2].volatile == twelve_hour[2].volatile == restored[2].volatile
    for frame in frames:
        assert all(frame[0][name] is first[0][name] for name in first[4])
        assert frame[4] == first[4]  # the same clients, not just equivalent new ones
        assert frame[5] == first[5]  # no client was polled early
        assert "Works at 18:00" in frame[3]
    assert any(text.endswith("21:00") for text in first[3])
    assert any(text.endswith("9:00 PM") for text in twelve_hour[3])
    if orientation == "vertical":
        assert {"07:58", "16:22"} <= first[3]
        assert {"7:58 AM", "4:22 PM"} <= twelve_hour[3]
    else:
        assert any(text.endswith((" AM", " PM")) and "until" not in text for text in twelve_hour[3])
    assert loop.api_reg.clients["weather"]._next_page == 1
    assert _calls_to(FORECAST_URL) == 1
    assert _calls_to(f"{GLOWMARKT_URL}/resource") == 1
    assert _calls_to("https://api.tfl.gov.uk/StopPoint/490000123W/Arrivals") == 1
    assert loop.interval == 1
    assert loop.display.target.paints == [None, None, None]
    loop.display.display_screen(
        restored[0], loop.config.screen, loop.api_reg.available(), clock_format=loop.config.clock_format
    )
    assert loop.display.target.paints == [None, None, None]  # unchanged pictures are still skipped


@responses.activate
def test_the_layout_follows_what_the_config_lets_the_device_show(isolated_cwd, monkeypatch):
    """Boots with Glowmarkt set up but not Spotify (GlowLayout); the owner then removes
    Glowmarkt's credentials on the broker, and the next cycle drops the energy column
    (BaseLayout, with neither). The screen is hung landscape: those are its layouts.
    """
    _mock_tfl_and_glowmarkt({}, {})
    landscape = {"orientation": "horizontal", "width": 800, "height": 480}
    responses.add(responses.GET, f"{TEST_BROKER_URL}/api/config", json=broker_payload(interval=1, screen=landscape))
    config = _make_config()
    config.screen = Screen.model_validate(landscape)
    loop = _build_loop(config, monkeypatch)
    layouts: list[type] = []
    _spy_on_frames(monkeypatch, layouts)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert layouts == [GlowLayout, BaseLayout]
    # BaseLayout has room for four stops, and the TfL client was told so.
    assert loop.api_reg.clients["tfl"].stops_per_update == BaseLayout.stops_shown == 4


@responses.activate
def test_the_first_cycle_reports_how_long_startup_took_once(isolated_cwd, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    responses.add(
        responses.GET,
        f"{TEST_BROKER_URL}/api/config",
        json=_app_config_json(pairing_code="ABC123"),
    )
    loop = _build_loop(make_config(), monkeypatch, pairing_code="ABC123")
    monkeypatch.setattr(loop.display, "display_pairing_screen", lambda panel, screen: None)

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

    assert loop.api_reg.status.stage == stage


def _record_publishes(loop: DisplayLoop, monkeypatch) -> list[str]:
    """The stage each time the loop asks the publisher to publish early."""
    stages: list[str] = []
    monkeypatch.setattr(loop.api_reg.pub, "publish_soon", lambda: stages.append(loop.api_reg.status.stage))
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
    monkeypatch.setattr(loop.display, "display_pairing_screen", lambda panel, screen: None)
    published = _record_publishes(loop, monkeypatch)

    _run_cycles(loop, monkeypatch, cycles=2)

    assert published == ["pairing", "running"]


@responses.activate
def test_refreshing_the_config_records_when_the_broker_last_answered(isolated_cwd, monkeypatch):
    _mock_config()
    loop = _build_loop(make_config(), monkeypatch)
    assert loop.api_reg.status.last_broker_sync is None

    _run_cycles(loop, monkeypatch, cycles=2)

    assert loop.api_reg.status.last_broker_sync is not None


@responses.activate
@pytest.mark.parametrize("pairing_code", ["ABC123", None], ids=["pairing", "dashboard"])
def test_run_retries_a_screen_the_panel_could_not_take_every_cycle(isolated_cwd, monkeypatch, pairing_code):
    _mock_config(pairing_code=pairing_code)
    loop = _build_loop(make_config(), monkeypatch, pairing_code=pairing_code)
    retries = []
    monkeypatch.setattr(loop.display, "repaint_pending", lambda: retries.append(1))

    _run_cycles(loop, monkeypatch, cycles=2)

    assert len(retries) == 2
