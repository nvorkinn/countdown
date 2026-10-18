"""The per-screen loop: it calls the render on the minute, on the broker's nudge, and faster while a provider asks.

Each screen still has its own renderer process, which runs this loop until the pool replaces it
(docs/stateless-renderers.md, steps 6 and 9). Everything that draws is render.render, run to completion each time;
the loop only decides when to call it, keeps the config current (from the store, else GET /api/config, which also
gives a pairing code), and hands the frame on: to the display, which PUTs it to the broker, beside the record the
render wrote.

A render is due:

- on the minute, every minute: the pages turn on the minute (core.rotation), as the pool's ticker will render;
- at once, when the broker says this screen's config changed (`watcher`, the `v1:config:changed` nudge);
- every PACED_RENDER_S (on the multiples of it since the epoch) while a provider's pace key is live (keys.pace), as
  the developer provider's is during a release.

How often the screen polls is the broker's business alone (auth-broker's services/pace.py): the loop only passes the
pace on, as `X-Refresh-Hint` with each PUT, for brokers that still read it.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pydantic
import requests
from redis.exceptions import RedisError

from countdown_core.render import Outcome, Stage, render
from countdown_core.store import reader

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from countdown_api.models import ConfigResponse
    from countdown_core.config_server.broker_client import BrokerClient
    from countdown_core.core.display import DisplayController
    from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
    from countdown_core.store.config import ConfigWatcher

logger = logging.getLogger(__name__)

# Renders come every minute, on the minute.
RENDER_INTERVAL_S = 60
# While a provider asks for a faster refresh, they come this often instead: the fastest a screen is ever refreshed,
# whatever a provider asks for (the broker's floor too, HINT_FLOOR_S).
PACED_RENDER_S = 20
# The name the tests and the broker's docs know the floor by.
MIN_PACE_S = PACED_RENDER_S


def safe_fetch[T, F](func: Callable[[], T], fallback: F) -> T | F:
    """Return `func()`, or `fallback` (logged) if it fails with a network or validation error."""
    try:
        return func()
    except requests.exceptions.RequestException:
        logger.exception("Couldn't get the config from the broker")
        return fallback
    except pydantic.ValidationError:
        logger.exception("Pydantic validation error")
        return fallback


def next_render(now: datetime, *, paced: bool) -> datetime:
    """Return when the next render is due after `now`: the next minute, or while `paced`, the next PACED_RENDER_S.

    Both are whole multiples of the interval since the epoch, so a paced render lands on the minute too.
    """
    interval = PACED_RENDER_S if paced else RENDER_INTERVAL_S
    elapsed = now.timestamp() // interval + 1
    return datetime.fromtimestamp(elapsed * interval, tz=UTC)


def wall_clock() -> datetime:
    """Return the time, aware in UTC: what the loop schedules its renders by."""
    return datetime.now(UTC)


async def read_pace(pace_keys: Sequence[str]) -> int | None:
    """Return the shortest refresh pace, in seconds, that a provider asks for now; None if none does.

    None as well with no store, or one that can't be read: the screen then keeps its usual pace.
    """
    store = reader.store()
    if store is None or not pace_keys:
        return None
    try:
        return await asyncio.to_thread(reader.fastest_pace, store, list(pace_keys))
    except RedisError as e:
        logger.warning("Couldn't read the refresh pace from the store (%s); keeping the usual", type(e).__name__)
        return None


class DisplayLoop:
    """Calls the render when one is due, keeps the config current, and hands each frame to the display."""

    def __init__(  # noqa: PLR0913 -- what the boot hands over; the rest are keyword-only, for the tests
        self,
        broker: BrokerClient,
        config: ConfigResponse,
        display: DisplayController,
        publisher: OtlpPublisher | None = None,
        *,
        watcher: ConfigWatcher | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = wall_clock,
    ) -> None:
        """Start from the boot's `config`, painting on `display` and reporting to `publisher` (if given).

        `broker` gives the later configs, and the device; `watcher`, if given, cuts the wait between renders short
        when the broker says the config changed. `sleep` and `clock` are what the loop waits with and schedules by.
        """
        self.broker = broker
        self.device_id = broker.device_id
        self.config = config
        self.display = display
        self.publisher = publisher
        self.watcher = watcher
        self._sleep = sleep
        self._clock = clock
        # The last dashboard the render drew, whose panels stand in for a client that fails in the next one.
        self.last: Outcome | None = None
        # How fast the providers ask for the screen to refresh as of the last render, in seconds; None: not at all.
        self.pace: int | None = None
        # When the render being waited for is due; None after a nudge, which renders at once.
        self._due: datetime | None = None
        self._published_stage: str | None = None

    async def run(self) -> None:
        """Render, wait until the next render is due (or the broker's nudge), re-read the config, and again; forever."""
        if self.watcher is not None:
            self.watcher.start()
        try:
            await self._run()
        finally:
            if self.watcher is not None:
                await self.watcher.close()

    async def _run(self) -> None:
        started = time.monotonic()
        first_cycle_done = False
        while True:
            await self.cycle()
            if not first_cycle_done:
                # Logged once, to spot a slow start without spamming the journal.
                logger.info("First cycle finished %.1fs after start", time.monotonic() - started)
                first_cycle_done = True
            await self.wait_for_next()
            await self.refresh_broker_config()

    async def cycle(self) -> Outcome | None:
        """Render the screen now, and hand the frame to the display; None if the render failed (logged)."""
        now = self._clock()
        if self._due is not None and now < self._due:
            now = self._due  # woken a moment early: draw the minute the render is for, not the one before
        try:
            # In a thread, so the nudge keeps listening meanwhile. render itself starts none.
            outcome = await asyncio.to_thread(render, self.device_id, now, config=self.config, previous=self.last)
        except Exception:
            logger.exception("The render failed")
            return None
        if outcome.stage is Stage.RUNNING:
            self.last = outcome
        self.pace = None if outcome.stage is Stage.PAIRING else await read_pace(outcome.pace_keys)
        self._report(outcome)
        if outcome.frame is not None:
            self.display.set_pace(self.pace)
            try:
                self.display.show(outcome.frame)
            except requests.exceptions.RequestException:
                logger.exception("Couldn't hand the frame to the broker")
            except Exception:
                logger.exception("Unexpected error showing the frame")
        return outcome

    def _report(self, outcome: Outcome) -> None:
        """Hand the render's statuses and stage to the publisher; a new stage is published at once."""
        if self.publisher is None:
            return
        self.publisher.statuses = outcome.statuses
        if outcome.stage is not None:
            self.publisher.device_status.stage = outcome.stage.value
        # The publisher reports every minute by itself; a new stage is worth reporting at once.
        stage = self.publisher.device_status.stage
        if stage != self._published_stage:
            self.publisher.publish_soon()
            self._published_stage = stage

    async def wait_for_next(self) -> bool:
        """Wait until the next render is due, or the broker says the config changed; return whether it did."""
        due = next_render(self._clock(), paced=self.pace is not None)
        seconds = max(0.0, (due - self._clock()).total_seconds())
        if self.watcher is None:
            await self._sleep(seconds)
            nudged = False
        else:
            nudged = await self.watcher.wait(seconds)
        self._due = None if nudged else due
        return nudged

    async def refresh_broker_config(self) -> None:
        """Re-read the config (from the store, else the broker), before every render.

        That also keeps the pairing code current. One that can't be had keeps the last.
        """
        fetched = await asyncio.to_thread(safe_fetch, self.broker.get_config, None)
        if fetched is None:
            return
        if self.publisher is not None:
            self.publisher.device_status.last_broker_sync = datetime.now(UTC)
        self.config = fetched
