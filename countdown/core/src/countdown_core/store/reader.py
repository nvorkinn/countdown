"""The renderer's side of the store: reading what the providers wrote, when this renderer is set up to.

A renderer reads the store only when it's given REDIS_URL (and REDIS_PASSWORD, if Redis wants one); the orchestrator
passes them on to the renderers it starts when it has them itself. Without REDIS_URL (a standalone Pi, which can't
reach the server's Redis, or a server that hasn't turned it on) every client calls its API directly, as before.

Synchronous (redis.Redis, not redis.asyncio): the clients' _initialise() and _update() run in worker threads.
"""

import functools
import logging
import os
from typing import TYPE_CHECKING

import redis

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Short: a slow Redis must not hold up a render cycle. A failed read raises, so the registry keeps the last panel.
TIMEOUT_S = 2


@functools.cache
def store() -> redis.Redis | None:
    """Return the client for the store this renderer reads, or None if it reads none (no REDIS_URL)."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    logger.info("Reading departures and weather from the store at %s", url)
    return redis.Redis.from_url(
        url,
        password=os.environ.get("REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_timeout=TIMEOUT_S,
        socket_connect_timeout=TIMEOUT_S,
    )


def read[M: BaseModel](client: redis.Redis, key: str, model: type[M]) -> M | None:
    """Return the model stored under `key`, or None if there's none (its provider is down or hasn't got to it yet).

    Raises if Redis can't be asked, or if what's there isn't a `model` (a provider on another version).
    """
    value = client.get(key)
    return None if value is None else model.model_validate_json(value)
