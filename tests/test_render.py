"""The render function (render.render): one screen's frame from the store, run to completion.

fixtures/render_parity.json is the parity check. It was recorded from the per-screen loop that preceded the render
function (DisplayLoop.run with ApiRegistry and DisplayController, countdown main at 1b3743d): for each case a config,
the store as the providers left it and a moment, and the etag of the frame that loop PUT. From the same inputs, with the
config in the store as the broker writes it, render(device_id, now) must draw exactly the same frames. The cases cover
both orientations and both clock formats, every panel, the developer panel above Spotify, English and Norwegian,
expired keys, a page of stops other than the first, a placed notice board, the pairing screen and a screen with nothing
set up.

Four cases were re-recorded from render() when the music area began folding away while nothing plays (and a message in
the notices' place stopped taking all their room): the player's two cases now have Spotify's position, so the track is
playing (with its progress bar); the top lists' case is on a minute they visit; the expired case has no music area.
Two more (the Norwegian case and the portrait card strip) were re-recorded when the notices' bullets went, their
rows became a table and the severity icon moved to the start of the text.

The rest: what an Outcome says, that a render sleeps, loops and threads not at all, and that a store that can't be read
fails the render and writes nothing.
"""

import asyncio
import base64
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from config_factory import make_config
from test_utils import put_departures

from countdown_api import fixtures
from countdown_api.models import ConfigResponse
from countdown_core import i18n
from countdown_core.store import frame_record, keys, reader
from countdown_renderer import moment, rotation
from countdown_renderer import render as render_module
from countdown_renderer.abstract_client import ClientStatus
from countdown_renderer.render import Outcome, Result, Stage, render

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "render_parity.json").read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
DEVICE_ID = FIXTURE["device_id"]

# Material Symbols stood in for (conftest), as when the fixture was recorded; and the moment is each case's own, so
# the pages' clock is the render's (conftest freezes it otherwise).
pytestmark = pytest.mark.usefixtures("symbols_font", "render_clock")


@pytest.fixture
def render_clock(monkeypatch):
    """Let the rotating panels turn by the moment being drawn (core.moment), as they do outside the tests."""
    monkeypatch.setattr(rotation, "now", moment.now)


@pytest.fixture(autouse=True)
def locale(monkeypatch):
    """Put the language and time zone back after each test: a render sets them, a module global."""
    monkeypatch.setattr(i18n, "_language", i18n.language())
    monkeypatch.setattr(i18n, "_timezone", i18n.local_zone())


def _fill(store, case: dict) -> None:
    """Put the case's store in the fake Redis, as the providers left it (a cover as base64), and its config."""
    binary = reader.binary_store()
    for key, value in case["store"].items():
        binary.set(key, base64.b64decode(value["base64"]) if isinstance(value, dict) else value)
    _put_config(store, _config(case))


def _put_config(store, config: ConfigResponse) -> None:
    """Store the config as the broker does: a live pairing code under its own key, anything else as the config."""
    key = keys.pairing(DEVICE_ID) if config.pairing_code else keys.config(DEVICE_ID)
    store.set(key, config.model_dump_json())


def _config(case: dict) -> ConfigResponse:
    return ConfigResponse.model_validate(case["config"])


def _now(case: dict) -> datetime:
    return datetime.fromisoformat(case["now"])


# --- parity: the same frames as the recorded loop ----------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_render_draws_the_frame_the_old_loop_drew(store, case):
    """As the pool's worker calls it: the device's id and the moment, and nothing else."""
    _fill(store, case)

    outcome = render(DEVICE_ID, _now(case))

    assert outcome.etag == case["etag"]
    assert outcome.result == Result.WRITTEN
    assert reader.binary_store().hget(keys.frame(DEVICE_ID), frame_record.ETAG) == case["etag"].encode()


def test_the_recorded_cases_cover_each_kind_of_screen():
    names = " ".join(case["name"] for case in CASES)
    kinds = {kind for case in CASES if case["panels"] for kind in case["panels"].values()}

    assert {"EnergyPanel", "WeatherPanel", "CombinedArrivalPanel", "SpotifyPanel", "SpotifyTopPanel"} <= kinds
    assert {"DeveloperPanel", "NoticeBoardPanel", "MessagePanel"} <= kinds
    assert all(word in names for word in ("landscape", "portrait", "12h", "Norwegian", "expired", "pairing"))
    assert len({case["etag"] for case in CASES}) == len(CASES)


# --- the render itself ------------------------------------------------------------------------------------------

RICH = next(case for case in CASES if case["name"].startswith("landscape"))


def test_the_same_inputs_draw_the_same_frame_and_the_second_writes_nothing(store):
    _fill(store, RICH)

    first = render(DEVICE_ID, _now(RICH))
    second = render(DEVICE_ID, _now(RICH))

    assert first.data == second.data
    assert (first.result, second.result) == (Result.WRITTEN, Result.UNCHANGED)


def test_the_render_sleeps_loops_and_threads_not_at_all(store, monkeypatch):
    """It runs to completion in its caller's thread: no sleep, no thread, no event loop of its own."""
    _fill(store, RICH)

    def refused(*args, **kwargs):
        msg = "a render must not wait, start a thread or run an event loop"
        raise AssertionError(msg)

    monkeypatch.setattr(time, "sleep", refused)
    monkeypatch.setattr(asyncio, "sleep", refused)
    monkeypatch.setattr(asyncio, "run", refused)
    monkeypatch.setattr(asyncio, "to_thread", refused)
    monkeypatch.setattr(threading.Thread, "start", refused)
    threads = threading.active_count()

    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome.etag == RICH["etag"]
    assert threading.active_count() == threads


def test_the_render_keeps_nothing_of_its_moment(store):
    _fill(store, RICH)

    render(DEVICE_ID, _now(RICH))

    assert abs(moment.now() - datetime.now(UTC)) < timedelta(seconds=5)  # the time again, not the case's


def test_a_device_with_no_config_in_the_store_has_nothing_to_draw(store):
    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome == Outcome(Result.NO_CONFIG)
    assert not reader.binary_store().exists(keys.frame(DEVICE_ID))


def test_without_a_store_there_is_nothing_to_draw(monkeypatch):
    monkeypatch.setattr(reader, "binary_store", lambda: None)
    monkeypatch.setattr(reader, "store", lambda: None)

    assert render(DEVICE_ID, _now(RICH)) == Outcome(Result.NO_CONFIG)


def test_with_nowhere_to_write_the_frame_it_is_drawn_but_unrecorded(store, monkeypatch):
    _fill(store, RICH)
    monkeypatch.setattr(reader, "binary_store", lambda: None)

    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome.result is Result.UNRECORDED
    assert outcome.etag == RICH["etag"]


def test_a_live_pairing_code_draws_the_pairing_screen_in_place_of_the_dashboard(store):
    _put_config(store, make_config())
    _put_config(store, make_config(pairing_code="K7Q2XP"))

    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome.stage is Stage.PAIRING
    assert outcome.result is Result.WRITTEN
    assert outcome.statuses == {}
    assert outcome.panels == {}
    assert outcome.frame.image.size == (800, 480)  # the panel's own landscape picture, for a portrait screen too


def test_once_the_code_goes_the_dashboard_is_drawn_again(store):
    _put_config(store, make_config())
    _put_config(store, make_config(pairing_code="K7Q2XP"))
    pairing = render(DEVICE_ID, _now(RICH))
    store.delete(keys.pairing(DEVICE_ID))  # used, lapsed, or the device paired

    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome.stage is Stage.RUNNING
    assert outcome.data != pairing.data


def test_the_outcome_says_what_the_metrics_report(store):
    case = next(case for case in CASES if case["name"].startswith("expired"))
    _fill(store, case)

    outcome = render(DEVICE_ID, _now(case))

    assert outcome.stage is Stage.RUNNING
    assert outcome.expired == {"glowmarkt", "weather", "spotify", "notice_board"}
    assert outcome.statuses == dict.fromkeys(
        ("notice_board", "glowmarkt", "tfl", "weather", "spotify"), ClientStatus.CONNECTED
    )


def test_the_departures_get_as_many_stops_as_the_layout_has_room_for(store):
    stops = [f"49000000{i}" for i in range(6)]
    for stop in stops:
        put_departures(store, stop, [("N1", "Somewhere", 300)], now=_now(RICH))
    landscape = {"orientation": "horizontal", "width": 800, "height": 480, "format": "mono-1bpp-packed"}
    _put_config(store, make_config(screen=landscape, tfl={"stop_ids": stops}))

    outcome = render(DEVICE_ID, _now(RICH))

    # The landscape layout without energy or Spotify has room for four (BaseLayout).
    assert len(outcome.panels["tfl"].arrival_panels) == 4


def test_with_the_room_taken_by_the_notices_the_departures_are_one_row_of_stops(store, monkeypatch):
    """The layout has room for four, but the notices took the second row's (Layout.stops_fitting): a page of two."""
    stops = [f"49000000{i}" for i in range(6)]
    for stop in stops:
        put_departures(store, stop, [("N1", "Somewhere", 300)], now=_now(RICH))
    landscape = {"orientation": "horizontal", "width": 800, "height": 480, "format": "mono-1bpp-packed"}
    _put_config(store, make_config(screen=landscape, tfl={"stop_ids": stops}))
    monkeypatch.setattr(render_module, "stops_fitting", lambda panels, screen, available: 2)

    outcome = render(DEVICE_ID, _now(RICH))

    assert len(outcome.panels["tfl"].arrival_panels) == 2
    assert outcome.panels["tfl"].rotation.pages == 3


def _stop_config(stops=("490000123W",), **overrides) -> ConfigResponse:
    landscape = {"orientation": "horizontal", "width": 800, "height": 480, "format": "mono-1bpp-packed"}
    return make_config(screen=landscape, tfl={"stop_ids": list(stops)}, **overrides)


def test_an_expired_key_is_not_available(store):
    put_departures(store, "490000123W", [("N155", "Morden", 300)], now=_now(RICH))
    _put_config(store, _stop_config())
    first = render(DEVICE_ID, _now(RICH))
    store.delete(keys.departures("490000123W"))

    expired = render(DEVICE_ID, _now(RICH))

    assert first.panels["tfl"].arrival_panels
    assert expired.panels["tfl"].message == "Not available"
    assert expired.expired == {"tfl", "notice_board"}


# --- a store that can't be read fails the render ---------------------------------------------------------------


def _departures_down(monkeypatch, store) -> None:
    """Make Redis fail every read of a departures key, as if it stopped answering mid-render; the config still reads."""
    get = store.get

    def failing(key, *args, **kwargs):
        if key.startswith("v1:departures:"):
            raise reader.redis.exceptions.ConnectionError
        return get(key, *args, **kwargs)

    monkeypatch.setattr(store, "get", failing)


def test_the_render_fails_and_writes_nothing_when_the_store_cant_be_read(store, monkeypatch):
    put_departures(store, "490000123W", [("N155", "Morden", 300)], now=_now(RICH))
    _put_config(store, _stop_config())
    first = render(DEVICE_ID, _now(RICH))
    before = reader.binary_store().hgetall(keys.frame(DEVICE_ID))
    _departures_down(monkeypatch, store)

    with pytest.raises(reader.redis.exceptions.ConnectionError):
        render(DEVICE_ID, _now(RICH) + timedelta(minutes=1))

    assert first.result is Result.WRITTEN
    # Not a frame saying it could not connect: the screen keeps the one it has.
    assert reader.binary_store().hgetall(keys.frame(DEVICE_ID)) == before


EVERY_PANEL = fixtures.load("config_store.json")["value"]


@pytest.mark.parametrize(
    "prefix",
    [
        "v1:departures:",
        "v1:weather:",
        "v1:energy:",
        "v1:spotify:",
        "v1:developer:",
        "v1:notices:",
        "v1:location:",
        "v1:",
    ],
)
def test_the_render_fails_whichever_panels_key_redis_cant_answer_for(store, monkeypatch, prefix):
    store.set(keys.config(DEVICE_ID), json.dumps(EVERY_PANEL))
    config_key = keys.config(DEVICE_ID)

    def failing(name):
        real = getattr(store, name)

        def call(*args, **kwargs):
            names = args[0] if isinstance(args[0], list) else [args[0]]
            if any(key.startswith(prefix) and key != config_key for key in names):
                raise reader.redis.exceptions.TimeoutError
            return real(*args, **kwargs)

        return call

    for name in ("get", "mget"):
        monkeypatch.setattr(store, name, failing(name))

    with pytest.raises(reader.redis.exceptions.RedisError):
        render(DEVICE_ID, _now(RICH))
    assert not reader.binary_store().exists(keys.frame(DEVICE_ID))


def test_the_render_still_draws_an_expired_key_not_available(store):
    store.set(keys.config(DEVICE_ID), _stop_config().model_dump_json())

    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome.result is Result.WRITTEN
    assert outcome.panels["tfl"].message == "Not available"
    assert "tfl" in outcome.expired
