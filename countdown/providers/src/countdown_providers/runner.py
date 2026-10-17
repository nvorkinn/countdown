"""The loop every provider runs in, written once: read the subscriptions, fetch them, write them to Redis, wait.

A cycle runs at once on start, then on every epoch-aligned tick of the provider's interval_s. A message on
`v1:sub:changed` (the broker saved a config) runs one early, and SIGINT/SIGTERM let the current cycle finish and then
stop. Nothing in a cycle crashes the process: a failed batch is logged and skipped (its keys just expire), and an
unreachable Redis is logged and tried again on the next tick.
"""

import asyncio
import logging
import signal
import time
from contextlib import suppress
from itertools import batched
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from countdown_core.store import keys
from countdown_providers.sharding import Shard

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel
    from redis.asyncio import Redis
    from redis.asyncio.client import Pipeline

    from countdown_providers.config import Settings
    from countdown_providers.provider import Provider

logger = logging.getLogger(__name__)

# Ids per provider.fetch() call: a failure loses at most this many, and each batch is written as soon as it's in.
DEFAULT_BATCH_SIZE = 10
# How long a pace a provider asked for (see provider.Provider) lasts unless the next cycle renews it: a few seconds
# over the shortest pace asked for, a cycle of the slowest provider that does.
PACE_TTL_S = 60
# How long to wait before resubscribing to `v1:sub:changed` after losing Redis.
RESUBSCRIBE_S = 5.0


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
        batch_size: int = DEFAULT_BATCH_SIZE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Fetch `shard`'s share of the ids (all of them by default), `batch_size` at a time; `clock` is for tests."""
        self.provider = provider
        self.redis = redis
        self.shard = shard or Shard()
        self.batch_size = batch_size
        self.clock = clock
        self._stopping = asyncio.Event()
        # Set by a `v1:sub:changed` message; cleared as each cycle starts, so one arriving mid-cycle runs another.
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
        wanted = await self.redis.smembers(keys.subscription(self.provider.name))
        # Sorted so batches (and logs) come out the same each cycle.
        mine = sorted(item_id for item_id in wanted if self.shard.owns(item_id))
        written = 0
        for batch in batched(mine, self.batch_size, strict=False):
            try:
                results = await self.provider.fetch(list(batch))
            except Exception:
                # Its keys aren't refreshed, so they expire (and their panels hide) if this keeps up.
                logger.exception("%s: couldn't fetch %d ids (%s...)", self.provider.name, len(batch), batch[0])
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
        logger.debug("%s: wrote %d of %d ids", self.provider.name, written, len(mine))
        return written

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
            logger.warning("%s: couldn't reach Redis (%s); trying again next tick", self.provider.name, e)

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
        """Set _nudged on every `v1:sub:changed` message, resubscribing (forever) if Redis goes away.

        A missed message only delays a change until the next tick, as every cycle re-reads the subscriptions anyway.
        """
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(keys.SUB_CHANGED)
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            logger.debug("%s: subscriptions changed", self.provider.name)
                            self._nudged.set()
            except RedisError as e:
                logger.warning("%s: lost %s (%s); resubscribing", self.provider.name, keys.SUB_CHANGED, e)
            await asyncio.sleep(RESUBSCRIBE_S)


async def serve(provider: Provider, settings: Settings) -> None:
    """Run `provider` with `settings` until SIGINT or SIGTERM, then close the Redis connection."""
    redis = settings.redis()
    runner = ProviderRunner(provider, redis, shard=settings.shard)
    loop = asyncio.get_running_loop()
    # As a container's PID 1 nothing handles SIGTERM unless we do, and docker stop would have to kill us.
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, runner.stop)
    try:
        await runner.run()
    finally:
        await redis.aclose()
