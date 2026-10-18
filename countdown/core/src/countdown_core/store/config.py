"""The screen's config in the store, which auth-broker writes, and its nudge when it changes.

The broker keeps its database as the source of truth and writes each screen's config to `v1:config:<device id>`
(keys.config): exactly what GET /api/config answers that device, the contract's ConfigResponse, less its secrets (the
API keys empty, no pairing code), with no TTL. It rewrites it on every config save and as it starts, then publishes
the device's id on `v1:config:changed` (keys.CONFIG_CHANGED). So a renderer reads its config here, needs the broker
only when there's nothing here (read() gives None: an older broker, or a pairing code to show, which only
GET /api/config gives), and redraws as soon as its owner saves (ConfigWatcher).

The value is self-contained: a fresh process can draw the screen from it and the providers' keys alone.
"""

import asyncio
import contextlib
import logging
import os
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from redis.exceptions import RedisError

from countdown_api.models import ConfigResponse
from countdown_core.store import keys, reader

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

# How long to wait before subscribing again after losing Redis.
RESUBSCRIBE_S = 5.0


def read(client: redis.Redis, device_id: str) -> ConfigResponse | None:
    """Return the device's config as the broker stored it, or None if there's none for it.

    Raises RedisError if Redis can't be asked, and pydantic's ValidationError if what's there isn't a config the
    contract allows (a broker on a newer contract that tightened something), as GET /api/config's answer would.
    """
    return reader.read(client, keys.config(device_id), ConfigResponse)


class ConfigWatcher:
    """Listens for the broker's `v1:config:changed` naming this device, so the display loop can wake when it does.

    The loop waits in wait(), which returns at its timeout or as soon as the broker says the config changed. A message
    missed while Redis was away only delays the change until the loop's next cycle, which reads the config anyway.
    """

    def __init__(self, client: Redis, device_id: str) -> None:
        """Listen with `client` (made with decode_responses=True) for `device_id`'s changes."""
        self.client = client
        self.device_id = device_id
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None

    @classmethod
    def from_env(cls, device_id: str) -> ConfigWatcher | None:
        """Return a watcher on the store REDIS_URL (and REDIS_PASSWORD) name, or None if there's no store."""
        url = os.environ.get("REDIS_URL", "")
        if not url:
            return None
        # No socket_timeout: a subscription waits for as long as nothing is published. A connect that hangs still
        # gives up, and is retried.
        client = Redis.from_url(
            url,
            password=os.environ.get("REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_connect_timeout=reader.TIMEOUT_S,
        )
        return cls(client, device_id)

    def start(self) -> None:
        """Start listening, in the background, until close()."""
        if self._task is None:
            self._task = asyncio.create_task(self._listen())

    async def wait(self, timeout: float) -> bool:  # noqa: ASYNC109 -- the loop's interval, which a nudge cuts short
        """Wait up to `timeout` seconds, or until the broker says the config changed; return whether it did.

        A change announced while the loop was busy (since the last wait) ends the next wait at once.
        """
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except TimeoutError:
            return False
        self._changed.clear()
        return True

    async def close(self) -> None:
        """Stop listening and close the connection."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.client.aclose()

    async def _listen(self) -> None:
        """Set _changed on every message naming this device, resubscribing (forever) if Redis goes away."""
        while True:
            try:
                async with self.client.pubsub() as pubsub:
                    await pubsub.subscribe(keys.CONFIG_CHANGED)
                    async for message in pubsub.listen():
                        if message["type"] == "message" and message["data"] == self.device_id:
                            logger.info("The broker says this screen's config changed")
                            self._changed.set()
            except RedisError as e:
                logger.warning("Lost %s (%s); resubscribing", keys.CONFIG_CHANGED, type(e).__name__)
            await asyncio.sleep(RESUBSCRIBE_S)
