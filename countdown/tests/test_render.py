"""The render function (render.render): one screen's frame from the store, run to completion, and the loop calling it.

fixtures/render_parity.json is the parity check. It was recorded from the per-screen loop as it was before the render
function (DisplayLoop.run with ApiRegistry and DisplayController, pi-dashboard main at 1b3743d), before that code
changed: for each case a config, the store as the providers left it and a moment, and the etag of the frame that loop
PUT. From the same inputs, render(device_id, now) must draw exactly the same frames, and so must the loop that now
calls it. The cases cover both orientations and both clock formats, every panel, the developer panel in the Spotify
area, English and Norwegian, expired keys, a page of stops other than the first, a placed notice board, the pairing
screen and a screen with nothing set up.

The rest: what an Outcome says, a failing client's last panel, that a render sleeps, loops and threads not at all, and
the per-screen loop's three triggers (the minute, the broker's nudge, and every 20 s while a pace key is live), through
DisplayLoop.run and the app's own boot.
"""

import asyncio
import base64
import json
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import responses
from config_factory import make_config
from fake_target import FakeTarget
from test_utils import put_departures

from countdown_api.models import ConfigResponse
from countdown_core import i18n
from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.core import app, display_loop, moment, rotation
from countdown_core.core.abstract_client import ClientStatus
from countdown_core.core.display import DisplayController
from countdown_core.core.display_loop import PACED_RENDER_S, RENDER_INTERVAL_S, DisplayLoop
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_core.render import Outcome, Result, Stage, render
from countdown_core.store import frame_record, keys, reader
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_credentials.registration import RendererRegistrar, RendererRegistration
from countdown_server.server_target import ServerTarget

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "render_parity.json").read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
DEVICE_ID = FIXTURE["device_id"]
BROKER_URL = "https://broker.example.com"
FRAME_URL = f"{BROKER_URL}/api/frame"
REGISTRATION = RendererRegistration(BROKER_URL, "shh", DEVICE_ID)

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
    """Put the case's store in the fake Redis, as the providers left it (a cover as base64)."""
    binary = reader.binary_store()
    for key, value in case["store"].items():
        binary.set(key, base64.b64decode(value["base64"]) if isinstance(value, dict) else value)


def _config(case: dict) -> ConfigResponse:
    return ConfigResponse.model_validate(case["config"])


def _now(case: dict) -> datetime:
    return datetime.fromisoformat(case["now"])


# --- parity: the same frames as the loop before it ----------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_render_draws_the_frame_the_old_loop_drew(store, case):
    _fill(store, case)

    outcome = render(DEVICE_ID, _now(case), config=_config(case))

    assert outcome.etag == case["etag"]
    assert outcome.result == Result.WRITTEN
    assert reader.binary_store().hget(keys.frame(DEVICE_ID), frame_record.ETAG) == case["etag"].encode()


# The store's copy of a config never has a pairing code: the pool's will come from `v1:pairing:<device id>` (step 7).
DASHBOARDS = [case for case in CASES if case["config"]["pairing_code"] is None]


@pytest.mark.parametrize("case", DASHBOARDS, ids=[case["name"] for case in DASHBOARDS])
def test_the_render_draws_the_same_from_the_config_in_the_store(store, case):
    """As the pool will call it: the device's id and the moment, and nothing else."""
    _fill(store, case)
    store.set(keys.config(DEVICE_ID), _config(case).model_dump_json())

    assert render(DEVICE_ID, _now(case)).etag == case["etag"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_loop_puts_the_frame_the_old_loop_put(store, case):
    """DisplayLoop.run, as the app runs it on ServerTarget: its first frame is the one PUT before."""
    _fill(store, case)
    broker = BrokerClient(REGISTRATION)

    async def stop(_seconds):
        raise _Stop

    loop = DisplayLoop(broker, _config(case), DisplayController(ServerTarget(REGISTRATION)), sleep=stop)
    loop._clock = lambda: _now(case)
    with responses.RequestsMock() as broker_http:
        broker_http.put(FRAME_URL, status=204)
        with pytest.raises(_Stop):
            asyncio.run(loop.run())
        [put] = [call.request.body for call in broker_http.calls]

    assert frame_record.frame_etag(put) == case["etag"]


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

    first = render(DEVICE_ID, _now(RICH), config=_config(RICH))
    second = render(DEVICE_ID, _now(RICH), config=_config(RICH))

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

    outcome = render(DEVICE_ID, _now(RICH), config=_config(RICH))

    assert outcome.etag == RICH["etag"]
    assert threading.active_count() == threads


def test_the_render_keeps_nothing_of_its_moment(store):
    _fill(store, RICH)

    render(DEVICE_ID, _now(RICH), config=_config(RICH))

    assert abs(moment.now() - datetime.now(UTC)) < timedelta(seconds=5)  # the time again, not the case's


def test_a_device_with_no_config_in_the_store_has_nothing_to_draw(store):
    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome == Outcome(Result.NO_CONFIG)
    assert not reader.binary_store().exists(keys.frame(DEVICE_ID))


def test_with_no_store_it_draws_but_writes_nothing(store, monkeypatch):
    monkeypatch.setattr(reader, "binary_store", lambda: None)
    monkeypatch.setattr(reader, "store", lambda: None)

    outcome = render(DEVICE_ID, _now(RICH), config=make_config())

    assert outcome.result == Result.UNRECORDED
    assert len(outcome.data) == 800 * 480 // 8


def test_a_pairing_code_draws_the_pairing_screen(store):
    outcome = render(DEVICE_ID, _now(RICH), config=make_config(pairing_code="K7Q2XP"))

    assert outcome.stage is Stage.PAIRING
    assert outcome.statuses == {}
    assert outcome.panels == {}
    assert outcome.frame.image.size == (800, 480)  # the panel's own landscape picture, for a portrait screen too


def test_the_outcome_says_what_the_metrics_report(store):
    case = next(case for case in CASES if case["name"].startswith("expired"))
    _fill(store, case)

    outcome = render(DEVICE_ID, _now(case), config=_config(case))

    assert outcome.stage is Stage.RUNNING
    assert outcome.expired == {"glowmarkt", "weather", "spotify", "notice_board"}
    assert outcome.statuses == dict.fromkeys(
        ("notice_board", "glowmarkt", "tfl", "weather", "spotify"), ClientStatus.CONNECTED
    )
    assert outcome.pace_keys == ()
    assert outcome.locale == ("en", "Europe/London")


def test_the_developer_panels_pace_key_is_in_the_outcome(store):
    outcome = render(DEVICE_ID, _now(RICH), config=make_config(developer={"enabled": True}))

    assert outcome.pace_keys == (keys.pace(keys.DEVELOPER, DEVICE_ID),)


def test_the_departures_get_as_many_stops_as_the_layout_has_room_for(store):
    stops = [f"49000000{i}" for i in range(6)]
    for stop in stops:
        put_departures(store, stop, [("N1", "Somewhere", 300)], now=_now(RICH))
    landscape = {"orientation": "horizontal", "width": 800, "height": 480}

    outcome = render(DEVICE_ID, _now(RICH), config=make_config(screen=landscape, tfl={"stop_ids": stops}))

    # The landscape layout without energy or Spotify has room for four (BaseLayout).
    assert len(outcome.panels["tfl"].arrival_panels) == 4


# --- a failing client draws the last render's panel ------------------------------------------------------------


def _stop_config(stops=("490000123W",), **overrides) -> ConfigResponse:
    landscape = {"orientation": "horizontal", "width": 800, "height": 480}
    return make_config(screen=landscape, tfl={"stop_ids": list(stops)}, **overrides)


def _redis_down(monkeypatch, store) -> None:
    def unreachable(*args, **kwargs):
        raise reader.redis.exceptions.ConnectionError

    monkeypatch.setattr(store, "get", unreachable)
    monkeypatch.setattr(store, "mget", unreachable)


def test_redis_not_answering_draws_the_last_renders_panel(store, monkeypatch):
    put_departures(store, "490000123W", [("N155", "Morden", 300)], now=_now(RICH))
    first = render(DEVICE_ID, _now(RICH), config=_stop_config())
    _redis_down(monkeypatch, store)

    kept = render(DEVICE_ID, _now(RICH), config=_stop_config(), previous=first)
    lost = render(DEVICE_ID, _now(RICH), config=_stop_config())

    assert kept.panels["tfl"] is first.panels["tfl"]
    assert kept.data == first.data
    assert "tfl" not in lost.panels  # nothing to fall back on: its area says it could not connect
    assert kept.data != lost.data


def test_the_last_panel_is_kept_through_render_after_render_while_redis_is_away(store, monkeypatch):
    put_departures(store, "490000123W", [("N155", "Morden", 300)], now=_now(RICH))
    outcome = render(DEVICE_ID, _now(RICH), config=_stop_config())
    first = outcome.panels["tfl"]
    _redis_down(monkeypatch, store)

    for _ in range(3):
        outcome = render(DEVICE_ID, _now(RICH), config=_stop_config(), previous=outcome)

    assert outcome.panels["tfl"] is first


def test_a_panel_from_another_config_is_never_drawn(store, monkeypatch):
    put_departures(store, "490000123W", [("N155", "Morden", 300)], now=_now(RICH))
    first = render(DEVICE_ID, _now(RICH), config=_stop_config())
    _redis_down(monkeypatch, store)

    other_stops = render(DEVICE_ID, _now(RICH), config=_stop_config(("490000999Z",)), previous=first)
    other_language = render(DEVICE_ID, _now(RICH), config=_stop_config(language="nb"), previous=first)

    assert "tfl" not in other_stops.panels
    assert "tfl" not in other_language.panels


def test_an_expired_key_is_not_available_rather_than_the_last_panel(store):
    put_departures(store, "490000123W", [("N155", "Morden", 300)], now=_now(RICH))
    first = render(DEVICE_ID, _now(RICH), config=_stop_config())
    store.delete(keys.departures("490000123W"))

    expired = render(DEVICE_ID, _now(RICH), config=_stop_config(), previous=first)

    assert isinstance(first.panels["tfl"], CombinedArrivalPanel)
    assert isinstance(expired.panels["tfl"], MessagePanel)
    assert expired.panels["tfl"].message == "Not available"
    assert expired.expired == {"tfl", "notice_board"}


# --- the render pool's render: the store unreachable fails it ------------------------------------------------


def _departures_down(monkeypatch, store) -> None:
    """Make Redis fail every read of a departures key, as if it stopped answering mid-render; the config still reads."""
    get = store.get

    def failing(key, *args, **kwargs):
        if key.startswith("v1:departures:"):
            raise reader.redis.exceptions.ConnectionError
        return get(key, *args, **kwargs)

    monkeypatch.setattr(store, "get", failing)


def test_the_pools_render_fails_and_writes_nothing_when_the_store_cant_be_read(store, monkeypatch):
    put_departures(store, "490000123W", [("N155", "Morden", 300)], now=_now(RICH))
    store.set(keys.config(DEVICE_ID), _stop_config().model_dump_json())
    first = render(DEVICE_ID, _now(RICH), raise_store_errors=True)
    before = reader.binary_store().hgetall(keys.frame(DEVICE_ID))
    _departures_down(monkeypatch, store)

    with pytest.raises(reader.redis.exceptions.ConnectionError):
        render(DEVICE_ID, _now(RICH) + timedelta(minutes=1), raise_store_errors=True)

    assert first.result is Result.WRITTEN
    # Not a frame saying it could not connect: the screen keeps the one it has.
    assert reader.binary_store().hgetall(keys.frame(DEVICE_ID)) == before


EVERY_PANEL = json.loads((Path(__file__).parent / "fixtures" / "config_store.json").read_text(encoding="utf-8"))[
    "value"
]


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
def test_the_pools_render_fails_whichever_panels_key_redis_cant_answer_for(store, monkeypatch, prefix):
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
        render(DEVICE_ID, _now(RICH), raise_store_errors=True)
    assert not reader.binary_store().exists(keys.frame(DEVICE_ID))


def test_without_raise_store_errors_a_client_redis_cant_answer_says_it_could_not_connect(store, monkeypatch):
    store.set(keys.config(DEVICE_ID), _stop_config().model_dump_json())
    _departures_down(monkeypatch, store)

    outcome = render(DEVICE_ID, _now(RICH))

    assert outcome.result is Result.WRITTEN
    assert "tfl" not in outcome.panels


def test_the_pools_render_still_draws_an_expired_key_not_available(store):
    store.set(keys.config(DEVICE_ID), _stop_config().model_dump_json())

    outcome = render(DEVICE_ID, _now(RICH), raise_store_errors=True)

    assert outcome.result is Result.WRITTEN
    assert outcome.panels["tfl"].message == "Not available"
    assert "tfl" in outcome.expired


# --- the per-screen loop's triggers, through DisplayLoop.run --------------------------------------------------


class _Stop(Exception):  # noqa: N818 -- not an error: how a test ends the loop
    """Raised by a fake sleep or wait to end DisplayLoop.run."""


class LoopClock:
    """The loop's clock and sleep: the time moves on by what the loop sleeps, and the test stops it after N sleeps."""

    def __init__(self, start: datetime, sleeps: int) -> None:
        self.at = start
        self.slept: list[float] = []
        self.sleeps = sleeps
        self.between: dict[int, object] = {}  # after the nth sleep, run this (a provider's doing)

    def now(self) -> datetime:
        return self.at

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if len(self.slept) > self.sleeps:
            raise _Stop
        self.at += timedelta(seconds=seconds)
        if (step := self.between.get(len(self.slept))) is not None:
            step()


def _loop(config: ConfigResponse, clock: LoopClock, watcher=None) -> tuple[DisplayLoop, list[datetime]]:
    """Return a loop on `config` (the broker can't be reached, so it stays), and the moment of each render it makes."""
    broker = BrokerClient(REGISTRATION)
    broker.get_config = lambda: config  # what the store holds for it
    loop = DisplayLoop(broker, config, DisplayController(FakeTarget()), sleep=clock.sleep, watcher=watcher)
    loop._clock = clock.now
    return loop, []


def _record_renders(monkeypatch) -> list[datetime]:
    moments: list[datetime] = []
    real = display_loop.render

    def recording(device_id, now, **kwargs):
        moments.append(now)
        return real(device_id, now, **kwargs)

    monkeypatch.setattr(display_loop, "render", recording)
    return moments


START = datetime(2026, 10, 18, 12, 0, 7, 250_000, tzinfo=UTC)


def test_the_loop_renders_on_the_minute(store, monkeypatch):
    clock = LoopClock(START, sleeps=3)
    loop, _ = _loop(make_config(), clock)
    moments = _record_renders(monkeypatch)

    with pytest.raises(_Stop):
        asyncio.run(loop.run())

    assert clock.slept[:3] == [52.75, 60, 60]  # to 12:01:00, then a minute each
    assert moments == [START, *(datetime(2026, 10, 18, 12, minute, tzinfo=UTC) for minute in (1, 2, 3))]


def test_a_sleep_that_wakes_early_still_draws_the_minute_it_was_for(store, monkeypatch):
    clock = LoopClock(START, sleeps=1)

    async def early(seconds: float) -> None:
        clock.slept.append(seconds)
        if len(clock.slept) > 1:
            raise _Stop
        clock.at += timedelta(seconds=seconds - 0.002)

    loop, _ = _loop(make_config(), clock)
    loop._sleep = early
    moments = _record_renders(monkeypatch)

    with pytest.raises(_Stop):
        asyncio.run(loop.run())

    assert moments[1] == datetime(2026, 10, 18, 12, 1, tzinfo=UTC)


def test_the_loop_renders_every_20_s_while_a_pace_key_is_live(store, monkeypatch):
    """The developer provider asks for a faster refresh while a release runs; the key lapses when it ends."""
    pace = keys.pace(keys.DEVELOPER, DEVICE_ID)
    store.set(pace, "20", ex=60)
    clock = LoopClock(START, sleeps=5)
    clock.between[3] = lambda: store.delete(pace)  # the release ends
    loop, _ = _loop(make_config(developer={"enabled": True}), clock)
    moments = _record_renders(monkeypatch)
    paces = []
    loop.display.target.set_pace = paces.append

    with pytest.raises(_Stop):
        asyncio.run(loop.run())

    assert clock.slept[:5] == [12.75, PACED_RENDER_S, PACED_RENDER_S, RENDER_INTERVAL_S, RENDER_INTERVAL_S]
    assert [moment.strftime("%M:%S") for moment in moments] == ["00:07", "00:20", "00:40", "01:00", "02:00", "03:00"]
    assert paces == [20, 20, 20, None, None, None]  # passed on as X-Refresh-Hint, for brokers that still read it


class FakeWatcher:
    """Stands in for ConfigWatcher: the broker's nudge comes during the waits listed in `nudged` (counted from 1)."""

    def __init__(self, clock: LoopClock, nudged: set[int]) -> None:
        self.clock, self.nudged, self.waits = clock, nudged, 0

    def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def wait(self, timeout: float) -> bool:  # noqa: ASYNC109 -- ConfigWatcher.wait's
        self.waits += 1
        if self.waits > self.clock.sleeps:
            raise _Stop
        if self.waits in self.nudged:
            self.clock.at += timedelta(seconds=5)  # the owner saves five seconds in
            return True
        self.clock.at += timedelta(seconds=timeout)
        return False


def test_the_brokers_nudge_renders_at_once_with_the_new_config(store, monkeypatch):
    clock = LoopClock(START, sleeps=2)
    configs = [make_config(style="hero"), make_config(style="card")]
    loop, _ = _loop(configs[0], clock, watcher=FakeWatcher(clock, nudged={1}))
    loop.broker.get_config = lambda: configs[1]  # what the owner saved
    styles = []
    real = display_loop.render

    def recording(device_id, now, **kwargs):
        styles.append((now, kwargs["config"].style))
        return real(device_id, now, **kwargs)

    monkeypatch.setattr(display_loop, "render", recording)

    with pytest.raises(_Stop):
        asyncio.run(loop.run())

    assert styles == [
        (START, "hero"),
        (START + timedelta(seconds=5), "card"),  # at once, not at the minute
        (datetime(2026, 10, 18, 12, 1, tzinfo=UTC), "card"),  # and then on the minute again
    ]


def test_a_render_that_fails_is_logged_and_the_loop_carries_on(store, monkeypatch, caplog):
    clock = LoopClock(START, sleeps=1)
    loop, _ = _loop(make_config(), clock)
    calls = []

    def broken(device_id, now, **kwargs):
        calls.append(now)
        msg = "a bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(display_loop, "render", broken)

    with pytest.raises(_Stop):
        asyncio.run(loop.run())

    assert len(calls) == 2
    assert "The render failed" in caplog.text
    assert loop.display.target.paints == []  # the screen keeps its frame


def test_the_loop_hands_each_renders_statuses_and_stage_to_the_metrics(store, monkeypatch):
    clock = LoopClock(START, sleeps=0)
    loop, _ = _loop(make_config(), clock)
    loop.publisher = OtlpPublisher(["tfl", "weather"])
    monkeypatch.setattr(loop.publisher, "publish_soon", lambda: None)

    with pytest.raises(_Stop):
        asyncio.run(loop.run())

    assert loop.publisher.device_status.stage == "running"
    assert loop.publisher.statuses == loop.last.statuses
    assert set(loop.publisher.statuses) == {"notice_board", "tfl", "weather"}
    assert loop.publisher.statuses["tfl"] == ClientStatus.CONNECTED


# --- the app's own boot, through to the loop calling the render -------------------------------------------------


def test_the_app_boots_into_the_loop_that_renders_and_puts_the_frame(store, monkeypatch):
    """app.run as the console script runs it, faked only where it would register, sleep or export."""
    monkeypatch.setenv("BROKER_URL", BROKER_URL)
    monkeypatch.setenv("CLAIM_TOKEN", "claim-me")
    monkeypatch.delenv("REDIS_URL", raising=False)  # no watcher: the store is conftest's fake

    async def registered(_registrar):
        return REGISTRATION

    async def config(_broker):
        return make_config(), None

    async def stop(_loop):
        raise _Stop

    monkeypatch.setattr(RendererRegistrar, "register", registered)
    monkeypatch.setattr(app, "wait_for_config", config)
    monkeypatch.setattr(app.OtlpPublisher, "start", lambda _publisher: None)
    monkeypatch.setattr(app.signal, "signal", lambda _signum, _handler: signal.SIG_DFL)
    monkeypatch.setattr(DisplayLoop, "wait_for_next", stop)

    with responses.RequestsMock() as broker_http:
        broker_http.put(FRAME_URL, status=204)
        with pytest.raises(_Stop):
            asyncio.run(app.run(ServerTarget))
        [put] = [call.request.body for call in broker_http.calls]

    assert reader.binary_store().hget(keys.frame(DEVICE_ID), frame_record.FRAME) == put
