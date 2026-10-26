"""The render pool: the `countdown-render-worker` and `countdown-render-ticker` console scripts.

Design: countdown/docs/stateless-renderers.md. Both run from the countdown-renderer image, as compose services on the
host (renderer/deploy/docker-compose.renderer.yml), with REDIS_URL and REDIS_PASSWORD:

- **The worker** is a thin wrapper round the render function: it blocks on the job queue (store.render_queue), claims
  each job, calls `render(device_id, now)` and releases the job, then records the metrics (pool_metrics). One render at
  a time, as the locale is a module global; each has a deadline (DEADLINE_S), past which faulthandler dumps where it
  was stuck and ends the process, for compose to start again. Its render raises rather than draw when the store can't
  be read, and writes nothing, so the screen keeps the frame it has.
- **The ticker** queues every screen in `v1:render:screens` on every minute, on the minute: the only clock on the render
  side. Running two by mistake is harmless, as enqueue coalesces.

Neither keeps anything between renders but the read-only caches (fonts, translations, Redis clients) and the metrics.
Logs are for what goes wrong: a render that fails, Redis unreachable, a screen with no config, a worker past its
deadline. There's no line per frame, and nothing logged says where a screen is (core.log_format).
"""

import faulthandler
import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime
from types import FrameType  # noqa: TC003 -- named in a signal handler's annotations, which are evaluated lazily
from typing import TYPE_CHECKING

import redis
from redis.exceptions import RedisError

from countdown_core import render as rendering
from countdown_core.core.log_format import configure_logging, error_reason, without_text
from countdown_core.store import render_queue
from countdown_renderer.pool_metrics import PoolMetrics

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# How long a worker waits on the queue before looking up to see if it's been asked to stop.
BLOCK_S = 2
# The most a render may take before the worker ends itself (compose restarts it). Under the lease (30 s), so a render
# that's still going always holds its screen's lease; well over a warm render's 50 ms, and a hung Redis's 2 s per read.
DEADLINE_S = 20
# The longest wait before asking an unreachable Redis again.
MAX_BACKOFF_S = 30
# The ticker's interval: the screens are drawn on every minute.
TICK_S = 60
# The socket timeouts of a client that doesn't block on the queue: a slow Redis mustn't hold anything up for long.
TIMEOUT_S = 2
DEFAULT_WORKER_NAME = "render-worker"


def connect(url: str, *, socket_timeout: float = TIMEOUT_S) -> redis.Redis:
    """Return a client (decoding values as text) for the store at `url`, with REDIS_PASSWORD if it's set."""
    return redis.Redis.from_url(
        url,
        password=os.environ.get("REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=TIMEOUT_S,
    )


def stop_on_signals(stopping: threading.Event) -> None:
    """Set `stopping` on SIGTERM or SIGINT. As a container's PID 1, nothing handles SIGTERM unless the process does."""

    def stop(_signum: int, _frame: FrameType | None) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


class Worker:
    """Takes jobs off the queue and renders them, one at a time, until `stopping` is set.

    `render` is render.render by default, and `now` the clock each render draws as of; tests replace them.
    """

    def __init__(  # noqa: PLR0913 -- each is one thing the loop needs, all but two keyword-only
        self,
        client: redis.Redis,
        metrics: PoolMetrics,
        *,
        stopping: threading.Event | None = None,
        render: Callable[..., rendering.Outcome] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        deadline_s: int = DEADLINE_S,
    ) -> None:
        """Take jobs from the store `client` (its socket timeout longer than BLOCK_S), and report to `metrics`."""
        self.client = client
        self.metrics = metrics
        self.stopping = stopping or threading.Event()
        self.render = render or rendering.render
        self.now = now
        self.deadline_s = deadline_s

    def run(self) -> None:
        """Wait for jobs and render them until `stopping` is set; an unreachable Redis is asked again, backing off."""
        logger.info("Starting the render worker: one render at a time, each within %ds", self.deadline_s)
        backoff = 1
        while not self.stopping.is_set():
            try:
                device_id = render_queue.next_job(self.client, BLOCK_S)
            except RedisError as e:
                logger.warning(
                    "Couldn't reach Redis for the next job (%s); trying again in %ds", error_reason(e), backoff
                )
                self.stopping.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                continue
            backoff = 1
            if device_id is not None:
                self.handle(device_id)
        logger.info("Stopped the render worker")

    def handle(self, device_id: str) -> None:
        """Claim the job for the screen, render it if it's this worker's to, release it and record how it went."""
        try:
            claimed = render_queue.claim(self.client, device_id)
        except RedisError as e:
            # Its queued flag and the job are gone; the next tick queues the screen again.
            logger.warning("Couldn't claim a job (%s); the next tick queues the screen again", error_reason(e))
            return
        if claimed.claim is render_queue.Claim.DROPPED:
            # A device the broker doesn't list in v1:render:screens (forgotten, or not paired).
            logger.debug("Dropped a job for %s, which the pool doesn't render", device_id)
            return
        if claimed.claim is render_queue.Claim.AGAIN:
            return  # its render is in flight elsewhere, which queues the follow-up as it ends
        slug = claimed.slug or device_id
        if claimed.waited_s is not None:
            self.metrics.waited(slug, claimed.waited_s)

        outcome = None
        started = time.monotonic()
        # In C, on a thread of its own, so it fires even if the render is stuck holding the GIL.
        faulthandler.dump_traceback_later(self.deadline_s, exit=True)
        try:
            outcome = self.render(device_id, self.now())
        except RedisError as e:
            logger.warning(
                "Couldn't read the store to render %s (%s); the screen keeps its frame", slug, error_reason(e)
            )
        except Exception as e:
            # By its reason and where, not its text: a config that doesn't validate would quote its postcode.
            logger.error("Couldn't render %s: %s", slug, error_reason(e), exc_info=without_text(e))  # noqa: TRY400
        finally:
            faulthandler.cancel_dump_traceback_later()
            duration_s = time.monotonic() - started
            self._release(device_id, slug, claimed.token)
        if outcome is not None and outcome.result is rendering.Result.NO_CONFIG:
            logger.warning("There's no config in the store for %s (%s); nothing to render", slug, device_id)
        self.metrics.rendered(device_id, slug, outcome, duration_s)

    def _release(self, device_id: str, slug: str, token: str) -> None:
        try:
            render_queue.release(self.client, device_id, token)
        except RedisError as e:
            logger.warning("Couldn't release %s's render (%s); its lease lapses by itself", slug, error_reason(e))


class Ticker:
    """Queues every screen the pool renders on every minute, on the minute, until `stopping` is set."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        stopping: threading.Event | None = None,
        clock: Callable[[], float] = time.time,
        interval_s: int | None = None,
    ) -> None:
        """Queue the screens in the store `client`, by `clock` (epoch seconds), every `interval_s` (TICK_S)."""
        self.client = client
        self.stopping = stopping or threading.Event()
        self.clock = clock
        self.interval_s = interval_s or TICK_S

    def tick(self) -> int:
        """Queue every screen in `v1:render:screens` not queued already; return how many this queued."""
        return render_queue.enqueue_all(self.client, render_queue.screens(self.client))

    def run(self) -> None:
        """Tick on every multiple of the interval, until `stopping` is set; an unreachable Redis waits for the next."""
        logger.info("Starting the render ticker: every %ds, on the minute", self.interval_s)
        while self._sleep_until((self.clock() // self.interval_s + 1) * self.interval_s):
            try:
                queued = self.tick()
            except RedisError as e:
                logger.warning("Couldn't queue the pool's screens (%s); trying again next minute", error_reason(e))
                continue
            logger.debug("Queued %d screens", queued)
        logger.info("Stopped the render ticker")

    def _sleep_until(self, due: float) -> bool:
        """Sleep until `clock` reaches `due` (never a moment early); False if stopped first."""
        while (remaining := due - self.clock()) > 0:
            if self.stopping.wait(remaining):
                return False
        return not self.stopping.is_set()


def run_worker() -> None:
    """Run the `countdown-render-worker` console script: REDIS_URL is required, WORKER_NAME names it in the metrics."""
    name = os.environ.get("WORKER_NAME") or DEFAULT_WORKER_NAME
    configure_logging({"worker": name})
    # Its socket timeout is over BLOCK_S, the queue's wait, which would otherwise time out as a failure.
    client = connect(os.environ["REDIS_URL"], socket_timeout=BLOCK_S + TIMEOUT_S)
    stopping = threading.Event()
    stop_on_signals(stopping)
    metrics = PoolMetrics(
        name,
        screens=lambda: render_queue.screens(client).keys(),
        queued=lambda: render_queue.queued(client),
    )
    # Not shut down at exit: the SDK's final export would retry against a collector that's down, and log it. Its thread
    # is a daemon's.
    metrics.start()
    try:
        Worker(client, metrics, stopping=stopping).run()
    finally:
        client.close()


def run_ticker() -> None:
    """Run the `countdown-render-ticker` console script: REDIS_URL is required."""
    configure_logging({"worker": "render-ticker"})
    client = connect(os.environ["REDIS_URL"])
    stopping = threading.Event()
    stop_on_signals(stopping)
    try:
        Ticker(client, stopping=stopping).run()
    finally:
        client.close()


if __name__ == "__main__":
    run_worker()
