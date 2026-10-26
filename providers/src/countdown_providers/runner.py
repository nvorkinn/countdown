"""The loop every provider runs in, written once: read the subscriptions, fetch them, write them to Redis, wait.

A cycle runs at once on start, then on every epoch-aligned tick of the provider's interval_s. A message on
`v1:sub:changed` (the broker saved a config), or on a channel in the provider's `wakes_on` (provider-locations placed a
new postcode: `v1:location:changed`), runs one early, and SIGINT/SIGTERM let the current cycle finish and then stop. A
provider that `announces` publishes on that channel after writing a batch it says has news in it (see
provider.Provider).

Nothing in a cycle crashes the process: a failed batch is logged and skipped (its keys just expire), and an unreachable
Redis is logged and tried again on the next tick.

What's logged never says where a screen is (the logs go to the central log store). A failure is logged by its reason
and traceback, not its text, which for a failed request quotes the URL (a postcode's, its place's or its coordinates',
or TfL's app key) and for a failed Redis pipeline its keys and values. A failed batch is named by its first id only for
a source whose ids are stops (NAMED_SOURCES); a postcode or a weather place is withheld.
"""

import asyncio
import logging
import signal
import time
from contextlib import suppress
from itertools import batched
from typing import TYPE_CHECKING, cast

from redis.exceptions import RedisError

from countdown_providers.sharding import Shard
from countdown_shared.core.log_format import error_reason, without_text
from countdown_shared.store import keys

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import BaseModel
    from redis.asyncio import Redis
    from redis.asyncio.client import Pipeline

    from countdown_providers.config import Settings
    from countdown_providers.provider import Provider

logger = logging.getLogger(__name__)

# Ids per provider.fetch() call: a failure loses at most this many, and each batch is written as soon as it's in.
DEFAULT_BATCH_SIZE = 10
# How long a pace a provider asked for (see provider.Provider) lasts unless the next cycle renews it: a few seconds
# over the shortest pace asked for, a cycle of the slowest provider that does. The store contract's, as the broker
# reads it.
PACE_TTL_S = keys.PACE_TTL_S
# How long to wait before resubscribing to `v1:sub:changed` after losing Redis.
RESUBSCRIBE_S = 5.0
# The sources whose ids the logs may name: stop ids, which say which stop a screen shows. Any other's say where a
# screen, and someone's home, is (a postcode, a weather place: a town, or typed text, which can be a postcode), so a
# failed batch of them is logged by its size alone. A provider with fixed_ids reads no source, so isn't named either.
NAMED_SOURCES = frozenset({keys.TFL, keys.ENTUR})


def next_tick(now: float, interval_s: float) -> float:
    """Return the first multiple of `interval_s` after `now` (epoch seconds): when the next cycle is due."""
    return (now // interval_s + 1) * interval_s


class ProviderRunner:
    """Runs `provider` against `redis` (made with decode_responses=True) until stop() is called."""

    def __init__(
        self,
        provider: Provider,
        redis: Redis,
        *,
        shard: Shard | None = None,
        batch_size: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Fetch `shard`'s share of the ids (all of them by default), `batch_size` at a time; `clock` is for tests.

        The batch size is, by default, the provider's own `batch_size` if it has one, else DEFAULT_BATCH_SIZE.
        """
        self.provider = provider
        self.redis = redis
        self.shard = shard or Shard()
        self.batch_size = batch_size or getattr(provider, "batch_size", DEFAULT_BATCH_SIZE)
        self.clock = clock
        self.names_ids = provider.name in NAMED_SOURCES
        # What wakes a cycle early: subscription changes (unless it has fixed_ids, which they can't change), and any
        # channel the provider asks for (wakes_on).
        reads_subscriptions = getattr(provider, "fixed_ids", None) is None
        self.wakes_on = (*([keys.SUB_CHANGED] if reads_subscriptions else []), *getattr(provider, "wakes_on", ()))
        self._stopping = asyncio.Event()
        # Set by a message on wakes_on; cleared as each cycle starts, so one arriving mid-cycle runs another.
        self._nudged = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop once the current cycle (if any) is done. Safe to call from a signal handler."""
        self._stopping.set()

    async def run(self) -> None:
        """Run cycles until stop() is called."""
        logger.info(
            "Starting the %s provider: every %ss, shard %d of %d",
            self.provider.name,
            self.provider.interval_s,
            self.shard.index,
            self.shard.count,
        )
        listener = asyncio.create_task(self._listen_for_changes())
        try:
            while not self._stopping.is_set():
                self._nudged.clear()
                await self._try_cycle()
                await self._wait_for_next_cycle()
        finally:
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
        logger.info("Stopped the %s provider", self.provider.name)

    async def run_cycle(self) -> int:
        """Fetch every subscribed id in this shard and write what came back; return how many keys were written.

        Raises RedisError if Redis can't be read or written; a failing provider only loses its batch.
        """
        wanted = await self._wanted()
        # Sorted so batches (and logs) come out the same each cycle.
        mine = sorted(item_id for item_id in wanted if self.shard.owns(item_id))
        written = 0
        for batch in batched(mine, self.batch_size, strict=False):
            try:
                results = await self.provider.fetch(list(batch))
            except Exception as e:
                # Its keys aren't refreshed, so they expire (and their panels hide) if this keeps up. Not
                # logger.exception, whose traceback ends in the error's text: this one ends in its reason.
                logger.error(  # noqa: TRY400 -- the traceback is there, without the text (see the module docstring)
                    "%s: couldn't fetch %d ids%s: %s",
                    self.provider.name,
                    len(batch),
                    f" ({batch[0]}...)" if self.names_ids else "",
                    error_reason(e),
                    exc_info=without_text(e),
                )
                continue
            # Only what was asked for: a provider can't write over another shard's (or source's) keys by mistake.
            fetched = {item_id: model for item_id, model in results.items() if item_id in batch}
            if not fetched:
                continue
            # One round trip for the batch. No transaction: each key stands alone.
            async with self.redis.pipeline(transaction=False) as pipe:
                for item_id, model in fetched.items():
                    pipe.set(self.provider.output_key(item_id), model.model_dump_json(), ex=self.provider.ttl_s)
                    self._write_pace(pipe, item_id, model)
                await pipe.execute()
            written += len(fetched)
            await self._announce(fetched)
        logger.debug("%s: wrote %d of %d ids", self.provider.name, written, len(mine))
        return written

    async def _wanted(self) -> set[str]:
        """Return the ids to fetch: the provider's fixed_ids if it has them (see provider.Provider), else its set's."""
        fixed = getattr(self.provider, "fixed_ids", None)
        if fixed is not None:
            return set(fixed)
        # Made with decode_responses=True (see the class), so members come back as str.
        return cast("set[str]", await self.redis.smembers(keys.subscription(self.provider.name)))

    async def _announce(self, written: Mapping[str, BaseModel]) -> None:
        """Publish on the provider's `announces` channel if any of what it just wrote is news (see provider.Provider).

        After the batch is written, so a provider woken by it reads the new values.
        """
        channel = getattr(self.provider, "announces", None)
        is_news = getattr(self.provider, "is_news", None)
        if channel is None or is_news is None:
            return
        # Every value asked about (not any(), which stops at the first): is_news remembers what it has said.
        news = [is_news(item_id, model) for item_id, model in written.items()]
        if any(news):
            await self.redis.publish(channel, "")
            logger.debug("%s: announced %d new of %d on %s", self.provider.name, sum(news), len(news), channel)

    def _write_pace(self, pipe: Pipeline, item_id: str, model: BaseModel) -> None:
        """Queue the pace the provider wants for `item_id`, if it asks for any: set while bursty, else deleted."""
        pace_for = getattr(self.provider, "pace", None)
        if pace_for is None:
            return
        key = keys.pace(self.provider.name, item_id)
        pace = pace_for(item_id, model)
        if pace:
            pipe.set(key, int(pace), ex=PACE_TTL_S)
        else:
            pipe.delete(key)

    async def _try_cycle(self) -> None:
        """Run a cycle, logging rather than raising if Redis can't be reached (the next tick tries again)."""
        try:
            await self.run_cycle()
        except RedisError as e:
            # A failed pipeline's text quotes its commands, each key and value: a place's, only by its reason.
            reason = e if self.names_ids else error_reason(e)
            logger.warning("%s: couldn't reach Redis (%s); trying again next tick", self.provider.name, reason)

    async def _wait_for_next_cycle(self) -> None:
        """Sleep until the next tick, or until a subscription change or stop() if sooner."""
        now = self.clock()
        delay = next_tick(now, self.provider.interval_s) - now
        waiters = [asyncio.create_task(self._stopping.wait()), asyncio.create_task(self._nudged.wait())]
        try:
            await asyncio.wait(waiters, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()

    async def _listen_for_changes(self) -> None:
        """Set _nudged on every message on a channel in wakes_on, resubscribing (forever) if Redis goes away.

        A missed message only delays a change until the next tick, as every cycle re-reads the subscriptions (and
        whatever else it reads) anyway. A provider with fixed_ids and no wakes_on has nothing to listen for.
        """
        if not self.wakes_on:
            return
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(*self.wakes_on)
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            logger.debug("%s: woken by %s", self.provider.name, message["channel"])
                            self._nudged.set()
            except RedisError as e:
                logger.warning("%s: lost %s (%s); resubscribing", self.provider.name, ", ".join(self.wakes_on), e)
            await asyncio.sleep(RESUBSCRIBE_S)


async def serve(provider: Provider, settings: Settings, *, redis: Redis | None = None) -> None:
    """Run `provider` with `settings` until SIGINT or SIGTERM, then close the Redis connection (see serve_all)."""
    await serve_all([provider], settings, redis=redis)


async def serve_all(providers: Sequence[Provider], settings: Settings, *, redis: Redis | None = None) -> None:
    """Run `providers` side by side, each in its own loop, until SIGINT or SIGTERM; then close the Redis connection.

    For one API's process that writes several kinds of key, each fetched at its own pace or for its own subscriptions
    (the TfL notices: the lines and the stations by stop, the roads by place).

    The runners use `redis` if given, else a client made from `settings`. A provider that reads the store as well
    (where a postcode is: locations.reader.LocationReader) is given the same client by its main(), passed here too,
    so the process has one connection pool, which this closes when they stop.
    """
    if redis is None:
        redis = settings.redis()
    runners = [ProviderRunner(provider, redis, shard=settings.shard) for provider in providers]

    def stop() -> None:
        for runner in runners:
            runner.stop()

    loop = asyncio.get_running_loop()
    # As a container's PID 1 nothing handles SIGTERM unless we do, and docker stop would have to kill us.
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop)
    try:
        await asyncio.gather(*(runner.run() for runner in runners))
    finally:
        await redis.aclose()
