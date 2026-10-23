"""End-to-end tests for the render, through to a rendered image, as the pool's worker calls it.

render.render(device_id, now) reads the screen's config from a fake store (conftest's `store`), where the tests put it
as the broker does, and builds the real clients afresh each time, reading what the tests put there as the providers
would. Nothing is asked of any other service: `responses` refuses every request.
"""

import datetime
import io
import json
import socket
from typing import TYPE_CHECKING

import pytest
import responses
from config_factory import make_config
from PIL import Image, ImageDraw, ImageFont
from redis.exceptions import ConnectionError as RedisConnectionError
from test_utils import put_departures
from weather_factory import forecast_json

from countdown_api.models import ConfigResponse, Screen
from countdown_core import i18n
from countdown_core import render as render_module
from countdown_core.core import rotation
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.render import Outcome, Result, Stage
from countdown_core.rendering import layout_for
from countdown_core.rendering.layouts.classic import BaseLayout, GlowLayout
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.store import keys, reader
from countdown_core.store.location import StoredLocation
from countdown_core.store.tfl_notices import StoredLineStatus, StoredStationDisruptions
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.tfl.models import Postcode
from countdown_core.utils import utils
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# Material Symbols stood in for (conftest), so every frame draws offline too: these are about what's drawn when.
pytestmark = pytest.mark.usefixtures("symbols_font")

DEVICE_ID = "test-device"


BUS_STOP = "490000123W"
TUBE_STATION = "940GZZLUKNG"


TRACK_JSON = {
    "id": "test-song",
    "name": "Test Song",
    "artists": [{"name": "Test Artist"}],
    "album": {"name": "Test Album", "images": [{"width": 64, "height": 64, "url": "https://example.com/album.jpg"}]},
    "duration_ms": 200_000,
}


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
    """Store Spotify's player (and top lists, the same for both periods) as auth-broker's music provider would.

    A track in the queue is playing, as of the pages' clock (rotation.now).
    """
    fetched_at = datetime.datetime.now(datetime.UTC).isoformat()
    player = {"fetched_at": fetched_at, "spotify": queue}
    if track := queue["currently_playing"]:
        read_at = rotation.now().isoformat()
        player["spotify_playback"] = {
            "fetched_at": read_at,
            "is_playing": True,
            "progress_ms": 0,
            "track_id": track["id"],
        }
    store.set(keys.spotify(DEVICE_ID), json.dumps(player))
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
    return make_config(glowmarkt={"enabled": True})


def _put_config(store, config: ConfigResponse) -> None:
    """Store the config as the broker does: a live pairing code under its own key, anything else as the config."""
    key = keys.pairing(DEVICE_ID) if config.pairing_code else keys.config(DEVICE_ID)
    store.set(key, config.model_dump_json())


def _spy_on_frames(monkeypatch, layouts: list[type] | None = None) -> list[dict]:
    """Record the panels dict of every frame drawn (and its layout, in `layouts`).

    The frame is still drawn and shown for real, so the whole render path is exercised too.
    """
    seen: list[dict] = []
    original = render_module.draw

    def spy(panels, screen, available, *, clock_format="24h"):
        seen.append(dict(panels))
        if layouts is not None:
            layouts.append(type(layout_for(screen, available)[0]))
        return original(panels, screen, available, clock_format=clock_format)

    monkeypatch.setattr(render_module, "draw", spy)
    return seen


def _render(store, config: ConfigResponse, *between: Callable[[], None], now=None) -> list[Outcome]:
    """Store `config`, render the screen, then again after each of `between` (the broker's or a provider's doing).

    Every render is as of `now`, or else the time.
    """
    _put_config(store, config)
    outcomes = [render_module.render(DEVICE_ID, now or datetime.datetime.now(datetime.UTC))]
    for step in between:
        step()
        outcomes.append(render_module.render(DEVICE_ID, now or datetime.datetime.now(datetime.UTC)))
    return outcomes


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
    shown = _spy_on_frames(monkeypatch)

    _render(store, config)

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
    config.tfl.stop_ids = [BUS_STOP]
    assert config.glowmarkt.enabled is False
    _put_stops_and_energy(store, BUS_STOP)
    shown = _spy_on_frames(monkeypatch)

    [outcome] = _render(store, config)

    assert keys.energy(DEVICE_ID) not in reads
    assert "glowmarkt" not in outcome.statuses  # never built while switched off
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
    shown = _spy_on_frames(monkeypatch)

    _render(store, config)

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
    shown = _spy_on_frames(monkeypatch)

    _render(store, config)

    assert isinstance(shown[0]["spotify"], SpotifyTopPanel)  # the player has nothing to show
    assert isinstance(shown[0]["glowmarkt"], EnergyPanel)


@responses.activate
def test_full_render_cycle_with_weather(isolated_cwd, monkeypatch, store):
    config = _make_config()
    config.weather.location = "London"
    _put_forecast(store, forecast_json())
    _put_energy(store)
    shown = _spy_on_frames(monkeypatch)

    _render(store, config)

    assert isinstance(shown[0]["weather"], WeatherPanel)
    assert shown[0]["weather"].weather.temperature == 14.2


@responses.activate
def test_full_render_cycle_reads_the_weather_under_the_brokers_place_key(isolated_cwd, monkeypatch, store, reads):
    """The broker sends the picked place's key beside its name, and subscribes the key: that's what's read."""
    config = _make_config()
    config.weather.location, config.weather.key = "London", "london-gb-2643743"
    _put_forecast(store, forecast_json(), location="london-gb-2643743")
    _put_energy(store)
    shown = _spy_on_frames(monkeypatch)

    _render(store, config)

    assert shown[0]["weather"].weather.temperature == 14.2
    assert keys.weather("london-gb-2643743") in reads
    assert keys.weather("London") not in reads


@responses.activate
def test_one_provider_being_down_does_not_stop_the_others_reaching_the_screen(isolated_cwd, monkeypatch, store):
    """The TfL provider is down, so its stop's key has expired; the energy readings are still there."""
    config = _make_config()
    config.tfl.stop_ids = [BUS_STOP]
    _put_energy(store)
    shown = _spy_on_frames(monkeypatch)

    _render(store, config)

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
    shown = _spy_on_frames(monkeypatch)

    _render(store, config, lambda: None)

    assert len(shown) == 2
    assert reads.count(keys.energy(DEVICE_ID)) == 2
    assert shown[1]["glowmarkt"] is not shown[0]["glowmarkt"]
    assert shown[1]["tfl"] is not shown[0]["tfl"]


@responses.activate
def test_a_pairing_code_draws_the_pairing_screen_and_nothing_else(isolated_cwd, monkeypatch, store, reads):
    """While the broker has a pairing code for the screen, nothing is read or drawn but the code."""
    _put_stops_and_energy(store, BUS_STOP)
    shown = _spy_on_frames(monkeypatch)

    [outcome] = _render(store, make_config(pairing_code="ABC123", tfl={"stop_ids": [BUS_STOP]}))

    assert outcome.stage is Stage.PAIRING
    assert shown == []
    assert reads == [keys.pairing(DEVICE_ID)]
    assert len(responses.calls) == 0


@responses.activate
def test_the_same_pairing_code_again_writes_nothing(isolated_cwd, store):
    first, again = _render(store, make_config(pairing_code="ABC123"), lambda: None)

    assert (first.result, again.result) == (Result.WRITTEN, Result.UNCHANGED)


@responses.activate
def test_config_changes_from_the_broker_reach_the_screen_on_the_next_render(isolated_cwd, monkeypatch, store):
    """The screen starts with nothing set up; the owner then adds a stop on the broker, which stores the config.

    The next render must pick that up.
    """
    _put_stops(store, BUS_STOP)
    shown = _spy_on_frames(monkeypatch)

    _render(store, make_config(), lambda: _put_config(store, make_config(tfl={"stop_ids": [BUS_STOP]})))

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
    expiry = datetime.datetime.fromisoformat("2026-09-25T21:00:00+01:00")
    notice_panel = NoticeBoardPanel(
        [
            Notice("Met Office", Severity.WARNING, "Wind until Fri", expiry, display_time=expiry),
            Notice("TfL", Severity.INFO, "Works at 18:00"),
        ]
    )
    monkeypatch.setattr(NoticeBoardClient, "_update", lambda self: notice_panel)
    # The same moment for each render, as if each came at once (as a save's does): only the clock format differs.
    stopped = datetime.datetime.now(datetime.UTC)
    texts, frames = [], []
    original_text = ImageDraw.ImageDraw.text
    original_render = render_module.draw

    def record_text(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    def record_frame(panels, screen, available, *, clock_format="24h"):
        texts.clear()
        frame = original_render(panels, screen, available, clock_format=clock_format)
        frames.append((dict(panels), clock_format, frame, set(texts)))
        return frame

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(render_module, "draw", record_frame)

    def save(clock_format: str) -> Callable[[], None]:
        return lambda: _put_config(store, config.model_copy(update={"clock_format": clock_format}))

    _render(store, config, save("12h"), save("24h"), now=stopped)

    assert [f[1] for f in frames] == ["24h", "12h", "24h"]
    first, twelve_hour, restored = frames
    assert first[2].image.tobytes() == restored[2].image.tobytes()
    assert first[2].image.tobytes() != twelve_hour[2].image.tobytes()
    for frame in frames:
        assert "Works at 18:00" in frame[3]
    assert any(text.endswith("21:00") for text in first[3])
    assert any(text.endswith("9:00 PM") for text in twelve_hour[3])
    # The energy chart's hours, beside the weather either way.
    assert any(text.endswith((" AM", " PM")) and "until" not in text for text in twelve_hour[3])
    # Each render reads what it draws afresh.
    assert reads.count(keys.weather("London")) == 3
    assert reads.count(keys.energy(DEVICE_ID)) == 3
    assert reads.count(keys.departures(BUS_STOP)) == 3


@responses.activate
def test_the_layout_follows_what_the_config_lets_the_device_show(isolated_cwd, monkeypatch, store):
    """Starts with Glowmarkt set up but not Spotify (GlowLayout), then loses Glowmarkt.

    The owner switches Glowmarkt off on the broker, and the next render drops the energy column (BaseLayout, with
    neither). The screen is hung landscape: those are its layouts.
    """
    _put_energy(store)
    landscape = {"orientation": "horizontal", "width": 800, "height": 480, "format": "mono-1bpp-packed"}
    config = _make_config()
    config.screen = Screen.model_validate(landscape)
    layouts: list[type] = []
    _spy_on_frames(monkeypatch, layouts)

    _render(store, config, lambda: _put_config(store, make_config(screen=landscape)))

    assert layouts == [GlowLayout, BaseLayout]


@pytest.fixture
def no_network(monkeypatch) -> Iterator[list[tuple]]:
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
def test_a_full_render_reaches_nothing_at_all(monkeypatch, store, no_network, place, country):
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
    shown = _spy_on_frames(monkeypatch)
    placed = []
    ensure_location = NoticeBoardClient._ensure_location

    def recording(board, now):
        ensure_location(board, now)
        placed.append(board.location)

    monkeypatch.setattr(NoticeBoardClient, "_ensure_location", recording)

    _render(store, config, lambda: None)

    assert no_network == []
    assert [call.request.url for call in responses.calls] == []
    assert len(shown) == 2
    assert placed == [place, place]  # each render's board placed from the store, not by asking anyone
    assert isinstance(shown[0]["tfl"], CombinedArrivalPanel)
    assert isinstance(shown[0]["weather"], WeatherPanel)
    assert isinstance(shown[0]["spotify"], SpotifyPanel)


# --- Expiry: the provider's TTL is all there is -----------------------------------------------------------------------


@responses.activate
def test_an_expired_key_draws_not_available_in_the_screens_language_and_redis_down_keeps_the_frame(
    monkeypatch, store, no_network
):
    """The whole render path: the render, the real clients reading the store, the layout and the drawing.

    The screen is in Norwegian. Its departures' key expires between the first and second frames (the TfL provider is
    down), while the energy readings stay: the second frame draws "Ikke tilgjengelig" where the departures were,
    translated as it's drawn, and none of the old departures. Then Redis stops answering: the third render fails and
    writes nothing, so the record keeps what the second drew, rather than a frame saying anything has expired.
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
    frames: list[tuple[dict, set[str], bytes]] = []
    texts: list[str] = []
    original_text = ImageDraw.ImageDraw.text
    original_render = render_module.draw

    def record_text(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    def record_frame(panels, screen, available, *, clock_format="24h"):
        texts.clear()
        frame = original_render(panels, screen, available, clock_format=clock_format)
        frames.append((dict(panels), set(texts), frame.image.tobytes()))
        return frame

    def redis_down() -> None:
        def unreachable(*args, **kwargs):
            raise RedisConnectionError

        monkeypatch.setattr(store, "get", unreachable)
        monkeypatch.setattr(store, "mget", unreachable)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(render_module, "draw", record_frame)

    _render(store, config, lambda: store.delete(keys.departures(BUS_STOP)))
    record = reader.binary_store().hgetall(keys.frame(DEVICE_ID))
    redis_down()
    with pytest.raises(RedisConnectionError):
        render_module.render(DEVICE_ID, datetime.datetime.now(datetime.UTC))

    assert len(frames) == 2
    (first, first_texts, _), (expired, expired_texts, _) = frames
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
    # Redis down: nothing drawn, nothing written; the screen keeps the second frame.
    assert reader.binary_store().hgetall(keys.frame(DEVICE_ID)) == record
