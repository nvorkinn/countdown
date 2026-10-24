"""The render pool's worker and ticker (countdown_renderer.render_pool), and their console scripts.

The worker's jobs go through the real queue (store.render_queue, on the fake Redis every test has) and the real render
function, drawing from the store as the providers left it. The entry points are run as they start: in this process
(run_worker / run_ticker, stopped by a real SIGTERM), and as the installed console scripts against a real redis-server
when there is one.
"""

import itertools
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from test_render_queue import redis_server  # noqa: F401 -- a fixture
from test_utils import put_departures

from countdown_api import fixtures
from countdown_core import i18n
from countdown_core import render as rendering
from countdown_core.core import log_format
from countdown_core.render import Result
from countdown_core.store import keys, reader, render_queue
from countdown_renderer import render_pool
from countdown_renderer.pool_metrics import FAILED, UNCHANGED, WRITTEN, PoolMetrics
from countdown_renderer.render_pool import Ticker, Worker

DEVICE, SLUG = "a1b2c3d4e5f6", "nvorkinn-test"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
# The config the broker stores for a screen with every panel on (countdown-server writes the same).
EVERY_PANEL = fixtures.load("config_store.json")["value"]
STOP_CONFIG = {
    **EVERY_PANEL,
    "screen": {"orientation": "horizontal", "width": 800, "height": 480, "format": "mono-1bpp-packed"},
    "tfl": {"stop_ids": ["490000123W"]},
}

pytestmark = pytest.mark.usefixtures("symbols_font")


@pytest.fixture(autouse=True)
def locale(monkeypatch):
    """Put the language and time zone back after each test: a render sets them, a module global."""
    monkeypatch.setattr(i18n, "_language", i18n.language())
    monkeypatch.setattr(i18n, "_timezone", i18n.local_zone())


@pytest.fixture
def pooled(store):
    """Put DEVICE in the pool, with its config in the store."""
    store.hset(keys.RENDER_SCREENS, DEVICE, SLUG)
    store.set(keys.config(DEVICE), json.dumps(STOP_CONFIG))
    return store


@pytest.fixture
def metric_reader():
    return InMemoryMetricReader()


@pytest.fixture
def metrics(store, metric_reader):
    pool = PoolMetrics(
        "render-worker-1",
        screens=lambda: render_queue.screens(store).keys(),
        queued=lambda: render_queue.queued(store),
        reader=metric_reader,
    )
    pool.start()
    yield pool
    pool.shutdown()


@pytest.fixture
def deadline(monkeypatch):
    """Stand in for faulthandler's deadline (it would end the test run), recording what it's asked."""
    calls = []
    monkeypatch.setattr(render_pool.faulthandler, "dump_traceback_later", lambda s, exit: calls.append((s, exit)))  # noqa: A006
    monkeypatch.setattr(render_pool.faulthandler, "cancel_dump_traceback_later", lambda: calls.append("cancel"))
    return calls


def worker(store, metrics, **kwargs) -> Worker:
    return Worker(store, metrics, now=lambda: NOW, **kwargs)


def counts(metric_reader) -> dict[str, int]:
    data = metric_reader.get_metrics_data()
    for scope in data.resource_metrics[0].scope_metrics if data and data.resource_metrics else []:
        for metric in scope.metrics:
            if metric.name == "countdown.render.count":
                return {p.attributes["outcome"]: p.value for p in metric.data.data_points}
    return {}


def pool_messages(caplog) -> list[str]:
    """Return what the worker itself logged (the render's clients log their own warnings, about expired keys)."""
    return [record.getMessage() for record in caplog.records if record.name == render_pool.__name__]


def job(store, device=DEVICE) -> str | None:
    render_queue.enqueue(store, device)
    return render_queue.next_job(store, 1)


# --- the worker ----------------------------------------------------------------------------------------------------


def test_a_job_renders_the_screen_from_the_store_and_writes_its_frame(pooled, metrics, metric_reader, deadline):
    put_departures(pooled, "490000123W", [("N155", "Morden", 300)], now=NOW)

    worker(pooled, metrics).handle(job(pooled))

    record = reader.binary_store().hgetall(keys.frame(DEVICE))
    assert len(record[b"frame"]) == 48_000
    assert counts(metric_reader) == {WRITTEN: 1}
    assert not pooled.exists(keys.render_lease(DEVICE))  # released
    assert deadline == [(render_pool.DEADLINE_S, True), "cancel"]


def test_the_same_picture_again_is_skipped_unchanged(pooled, metrics, metric_reader, deadline):
    a_worker = worker(pooled, metrics)
    a_worker.handle(job(pooled))
    a_worker.handle(job(pooled))

    assert counts(metric_reader) == {WRITTEN: 1, UNCHANGED: 1}


def test_an_expired_key_still_draws_not_available(pooled, metrics, metric_reader, deadline):
    calls = []

    def spy(*args, **kwargs):
        calls.append(outcome := rendering.render(*args, **kwargs))
        return outcome

    worker(pooled, metrics, render=spy).handle(job(pooled))

    [outcome] = calls
    assert outcome.result is Result.WRITTEN
    assert outcome.panels["tfl"].message == "Not available"


@pytest.mark.usefixtures("deadline")
def test_a_render_that_cant_read_the_store_fails_and_the_screen_keeps_its_frame(
    pooled, metrics, metric_reader, monkeypatch, caplog
):
    put_departures(pooled, "490000123W", [("N155", "Morden", 300)], now=NOW)
    a_worker = worker(pooled, metrics)
    a_worker.handle(job(pooled))
    kept = reader.binary_store().hgetall(keys.frame(DEVICE))
    get = pooled.get

    def departures_down(key, *args, **kwargs):
        if key.startswith("v1:departures:"):
            raise redis.exceptions.ConnectionError
        return get(key, *args, **kwargs)

    monkeypatch.setattr(pooled, "get", departures_down)

    with caplog.at_level(logging.WARNING, logger=render_pool.__name__):
        a_worker.handle(job(pooled))

    assert reader.binary_store().hgetall(keys.frame(DEVICE)) == kept
    assert counts(metric_reader) == {WRITTEN: 1, FAILED: 1}
    assert pool_messages(caplog) == [
        f"Couldn't read the store to render {SLUG} (ConnectionError); the screen keeps its frame"
    ]
    assert not pooled.exists(keys.render_lease(DEVICE))


def test_a_render_that_raises_is_logged_by_its_reason_not_its_text(pooled, metrics, metric_reader, deadline, caplog):
    def broken(*args, **kwargs):
        msg = "postcode SE17 3LL"
        raise ValueError(msg)

    with caplog.at_level(logging.WARNING, logger=render_pool.__name__):
        worker(pooled, metrics, render=broken).handle(job(pooled))

    [record] = [record for record in caplog.records if record.name == render_pool.__name__]
    assert record.getMessage() == f"Couldn't render {SLUG}: ValueError"
    formatted = log_format.JsonFormatter().format(record)
    assert "SE17" not in formatted
    assert "broken" in formatted  # where it was raised
    assert counts(metric_reader) == {FAILED: 1}
    assert not pooled.exists(keys.render_lease(DEVICE))
    assert deadline[-1] == "cancel"


def test_a_screen_with_no_config_is_a_failure_and_logged(pooled, metrics, metric_reader, deadline, caplog):
    pooled.delete(keys.config(DEVICE))

    with caplog.at_level(logging.WARNING, logger=render_pool.__name__):
        worker(pooled, metrics).handle(job(pooled))

    assert pool_messages(caplog) == [f"There's no config in the store for {SLUG} ({DEVICE}); nothing to render"]
    assert counts(metric_reader) == {FAILED: 1}


def test_a_job_for_a_screen_the_pool_doesnt_render_is_dropped_unrendered(pooled, metrics, metric_reader, deadline):
    renders = []

    worker(pooled, metrics, render=lambda *a, **k: renders.append(a)).handle(job(pooled, "0f9e8d7c6b5a"))

    assert renders == []
    assert deadline == []
    assert counts(metric_reader) == {}


def test_a_job_that_comes_mid_render_leaves_exactly_one_follow_up(pooled, metrics, deadline):
    a_worker = worker(pooled, metrics)
    other = worker(pooled, metrics)
    mid_render = []

    def render_with_jobs_arriving(*args, **kwargs):
        for _ in range(3):  # ticks and nudges, each popped by the other worker
            other.handle(job(pooled))
        mid_render.append(render_queue.queued(pooled))
        return rendering.render(*args, **kwargs)

    a_worker.render = render_with_jobs_arriving
    a_worker.handle(job(pooled))

    assert mid_render == [0]  # the other worker drew nothing: each job left the flag
    assert pooled.lrange(keys.RENDER_JOBS, 0, -1) == [DEVICE]


def test_the_wait_in_the_queue_is_recorded(pooled, metrics, metric_reader, deadline):
    worker(pooled, metrics).handle(job(pooled))

    data = metric_reader.get_metrics_data()
    names = {m.name for s in data.resource_metrics[0].scope_metrics for m in s.metrics}
    assert "countdown.render.lag" in names


def test_the_worker_runs_until_stopped_and_backs_off_while_redis_is_away(store, metrics, monkeypatch, caplog):
    stopping = threading.Event()
    waits = []
    answers = iter([redis.exceptions.ConnectionError(), redis.exceptions.ConnectionError(), None])

    def next_job(client, timeout_s):
        answer = next(answers, None)
        if isinstance(answer, Exception):
            raise answer
        stopping.set()
        return answer

    monkeypatch.setattr(render_queue, "next_job", next_job)
    monkeypatch.setattr(stopping, "wait", waits.append)

    with caplog.at_level(logging.INFO, logger=render_pool.__name__):
        Worker(store, metrics, stopping=stopping).run()

    assert waits == [1, 2]
    assert caplog.messages[0].startswith("Starting the render worker")
    assert caplog.messages[1] == "Couldn't reach Redis for the next job (ConnectionError); trying again in 1s"
    assert caplog.messages[-1] == "Stopped the render worker"


# --- the ticker ----------------------------------------------------------------------------------------------------


class TickClock:
    """Epoch seconds that move only by what the ticker waits (less `early`, as a sleep that wakes a moment early)."""

    def __init__(self, at: float, early: float = 0.0):
        self.at = at
        self.early = early
        self.ticks: list[float] = []

    def __call__(self) -> float:
        return self.at


class TickStopping(threading.Event):
    """Stops the ticker after `ticks` waits that reached their minute."""

    def __init__(self, clock: TickClock, ticks: int):
        super().__init__()
        self.clock = clock
        self.left = ticks

    def wait(self, timeout=None):
        self.clock.at += max(timeout - self.clock.early, 0.001)
        if self.left == 0:
            self.set()
        return self.is_set()


def test_the_ticker_queues_every_pooled_screen(pooled):
    pooled.hset(keys.RENDER_SCREENS, "0f9e8d7c6b5a", "someone-else")

    assert Ticker(pooled).tick() == 2
    assert sorted(pooled.lrange(keys.RENDER_JOBS, 0, -1)) == sorted([DEVICE, "0f9e8d7c6b5a"])
    assert Ticker(pooled).tick() == 0  # queued already: coalesced


def test_the_ticker_ticks_on_every_minute_and_never_a_moment_early(pooled, monkeypatch):
    clock = TickClock(at=1_800_000_015.5, early=0.002)
    stopping = TickStopping(clock, ticks=3)
    ticked = []

    def tick(self):
        ticked.append(clock.at)
        stopping.left -= 1
        render_queue.next_job(pooled, 1)  # a worker takes it
        return 1

    monkeypatch.setattr(Ticker, "tick", tick)

    Ticker(pooled, stopping=stopping, clock=clock).run()

    assert len(ticked) == 3
    assert all(at % 60 < 0.01 for at in ticked)  # on the minute, never before it
    assert [round(b - a) for a, b in itertools.pairwise(ticked)] == [60, 60]


def test_the_ticker_carries_on_when_redis_is_away(pooled, monkeypatch, caplog):
    clock = TickClock(at=1_800_000_000.0)
    stopping = TickStopping(clock, ticks=2)
    attempts = []

    def tick(self):
        attempts.append(clock.at)
        stopping.left -= 1
        raise redis.exceptions.ConnectionError

    monkeypatch.setattr(Ticker, "tick", tick)

    with caplog.at_level(logging.WARNING, logger=render_pool.__name__):
        Ticker(pooled, stopping=stopping, clock=clock).run()

    assert len(attempts) == 2
    assert caplog.messages == ["Couldn't queue the pool's screens (ConnectionError); trying again next minute"] * 2


# --- the entry points, as they start --------------------------------------------------------------------------------


@pytest.fixture
def signals():
    """Put SIGTERM's and SIGINT's handlers back after a test that runs an entry point, which sets them."""
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


def test_run_worker_renders_the_queued_screen_and_stops_on_sigterm(pooled, monkeypatch, signals, deadline):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("WORKER_NAME", "render-worker-2")
    monkeypatch.setattr(render_pool, "connect", lambda url, **kwargs: pooled)
    monkeypatch.setattr(pooled, "close", lambda: None)
    exporters = []
    monkeypatch.setattr(
        PoolMetrics, "start", lambda self: exporters.append(self.worker_name), raising=True
    )  # no exporter thread in the tests
    real_render = rendering.render

    def render_then_sigterm(*args, **kwargs):
        outcome = real_render(*args, **kwargs)
        signal.raise_signal(signal.SIGTERM)  # as docker stop does
        return outcome

    monkeypatch.setattr(rendering, "render", render_then_sigterm)
    monkeypatch.setattr(log_format.logging, "basicConfig", lambda **kwargs: None)  # leave pytest's logging alone
    render_queue.enqueue(pooled, DEVICE)

    render_pool.run_worker()

    assert exporters == ["render-worker-2"]
    assert pooled.hexists(keys.frame(DEVICE), "etag")


def test_run_ticker_stops_on_sigterm_while_it_waits_for_the_minute(pooled, monkeypatch, signals):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(render_pool, "connect", lambda url, **kwargs: pooled)
    monkeypatch.setattr(pooled, "close", lambda: None)
    monkeypatch.setattr(render_pool, "TICK_S", 3600)  # it would wait up to an hour for its first tick
    monkeypatch.setattr(log_format.logging, "basicConfig", lambda **kwargs: None)
    threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()
    started = time.monotonic()

    render_pool.run_ticker()

    assert time.monotonic() - started < 5


def test_the_entry_points_need_redis_url(monkeypatch, signals):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(log_format.logging, "basicConfig", lambda **kwargs: None)

    for entry_point in (render_pool.run_worker, render_pool.run_ticker):
        with pytest.raises(KeyError, match="REDIS_URL"):
            entry_point()


def _console_script(name: str) -> str:
    path = Path(sys.executable).parent / name
    if not path.exists():
        pytest.skip(f"{name} isn't installed")
    return str(path)


class Started:
    """A console script running, its stderr read as it comes (so a test can wait for a line before signalling it)."""

    def __init__(self, name: str, redis_url: str, **env):
        environment = {**os.environ, "REDIS_URL": redis_url, "REDIS_PASSWORD": "pw", "LOG_LEVEL": "INFO", **env}
        self.process = subprocess.Popen(  # noqa: S603 -- our own console script
            [_console_script(name)], env=environment, stderr=subprocess.PIPE, text=True
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=lambda: self.lines.extend(self.process.stderr), daemon=True)
        self._reader.start()

    def wait_for(self, text: str, timeout_s: float = 30) -> None:
        """Wait until it has logged a line containing `text`: its signal handlers are set by then."""
        deadline = time.monotonic() + timeout_s
        while not any(text in line for line in self.lines):
            assert time.monotonic() < deadline, self.lines
            assert self.process.poll() is None, self.lines
            time.sleep(0.05)

    def stop(self) -> list[dict]:
        """SIGTERM it, as docker stop does; it must exit 0. Return its JSON log lines."""
        self.process.send_signal(signal.SIGTERM)
        self.process.wait(15)
        self._reader.join(5)
        assert self.process.returncode == 0, self.lines
        return [json.loads(line) for line in self.lines if line.startswith("{")]


def test_the_worker_console_script_renders_a_pooled_screen_on_a_real_redis(redis_server):  # noqa: F811
    client = redis.Redis.from_url(redis_server, password="pw", decode_responses=True)
    client.flushall()
    # Nothing set up but the clock: drawn with the fonts the package ships, so it needs no download.
    client.set(keys.config(DEVICE), json.dumps({**EVERY_PANEL, "tfl": {"stop_ids": []}}))
    client.hset(keys.RENDER_SCREENS, DEVICE, SLUG)
    render_queue.enqueue(client, DEVICE)
    render_queue.enqueue(client, "0f9e8d7c6b5a")  # not pooled: dropped
    process = Started("countdown-render-worker", redis_server, WORKER_NAME="render-worker-1")
    process.wait_for("Starting the render worker")
    try:
        for _ in range(300):
            if client.hexists(keys.frame(DEVICE), "etag") and not client.llen(keys.RENDER_JOBS):
                break
            time.sleep(0.1)
    finally:
        lines = process.stop()

    assert client.hexists(keys.frame(DEVICE), "etag")
    assert not client.exists(keys.frame("0f9e8d7c6b5a"))
    assert not client.exists(keys.render_lease(DEVICE))
    messages = [line["_msg"] for line in lines]
    assert messages[0].startswith("Starting the render worker")
    assert messages[-1] == "Stopped the render worker"
    assert all(line["worker"] == "render-worker-1" for line in lines)
    # No line per frame: what's logged at info is the start, the store's address and the stop.
    assert [line["_msg"] for line in lines if line["level"] == "info"] == [
        messages[0],
        f"Reading the panels' data from the store at {redis_server}",
        "Stopped the render worker",
    ]


def test_the_ticker_console_script_starts_and_stops_cleanly_on_a_real_redis(redis_server):  # noqa: F811
    process = Started("countdown-render-ticker", redis_server)
    process.wait_for("Starting the render ticker")
    time.sleep(0.5)

    lines = process.stop()

    assert [line["_msg"] for line in lines] == [
        "Starting the render ticker: every 60s, on the minute",
        "Stopped the render ticker",
    ]
