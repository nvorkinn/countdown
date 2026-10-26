"""The renderer's side of the store: reading what the providers wrote.

The render pool's workers run on the server and read the store: compose gives them REDIS_URL (and REDIS_PASSWORD, if
Redis wants one). Every client reads only the store (require_store()); without REDIS_URL there's nothing to draw.

Each provider's TTL is the only expiry there is: a key that has expired (or isn't there yet) raises a NoStoredDataError,
and its panel says "Not available" (AbstractClient.poll), while Redis not answering raises a RedisError, which fails the
render, so the screen keeps the frame it has.

Synchronous (redis.Redis, not redis.asyncio): a render, and the clients' _initialise() and _update(), run to completion
in the worker's own thread.
"""

import functools
import logging
import os
from typing import TYPE_CHECKING

import redis

from countdown_core.store import file_store

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Short: a slow Redis must not hold up a render. A failed read raises, and fails the render.
TIMEOUT_S = 2


class StoreNotConfiguredError(RuntimeError):
    """The renderer was started without REDIS_URL, so it has no store to read its panels' data from."""


class NoStoredDataError(LookupError):
    """The store has nothing under a key a panel needs: its provider is down (the key expired) or hasn't written it yet.

    Every client's and notice source's own "nothing stored" error is one. The client's panel then says "Not available"
    (AbstractClient.poll), and the notice board leaves that source out: nothing is drawn from data older than its TTL.
    Unlike a KeyError or an IndexError, which are LookupErrors too but bugs.
    """


@functools.cache
def store() -> redis.Redis | None:
    """Return the client for the store this renderer reads, or None if it reads none (no REDIS_URL).

    It hands every value back as text (decode_responses), which everything but the covers is: JSON, or a number.
    """
    client = _connect(decode_responses=True)
    if client is not None:
        logger.info("Reading the panels' data from the store at %s", os.environ["REDIS_URL"])
    return client


@functools.cache
def binary_store() -> redis.Redis | None:
    """Return a client for the same store that hands values back as bytes, or None if there's no store (no REDIS_URL).

    For the values that aren't text: the covers (keys.album_art), image files, which store() would fail to decode as
    UTF-8. A client of its own, and so a connection of its own, because redis-py decodes per client, not per call.
    """
    return _connect(decode_responses=False)


def _connect(*, decode_responses: bool) -> redis.Redis | None:
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    if file_store.is_file_url(url):
        return file_store.connect(url, decode_responses=decode_responses)
    return redis.Redis.from_url(
        url,
        password=os.environ.get("REDIS_PASSWORD") or None,
        decode_responses=decode_responses,
        socket_timeout=TIMEOUT_S,
        socket_connect_timeout=TIMEOUT_S,
    )


def require_store() -> redis.Redis:
    """Return the client for the store, for a client that has nowhere else to get its data.

    Raises StoreNotConfiguredError without REDIS_URL. Only that: making the client doesn't connect, so Redis being
    down raises later (a RedisError), from each read, which fails the render.
    """
    client = store()
    if client is None:
        msg = "REDIS_URL is not set: this renderer reads its panels' data from the store, and has none to read"
        raise StoreNotConfiguredError(msg)
    return client


def read[M: BaseModel](client: redis.Redis, key: str, model: type[M]) -> M | None:
    """Return the model stored under `key`, or None if there's none (its provider is down or hasn't got to it yet).

    Raises if Redis can't be asked, or if what's there isn't a `model` (a provider on another version).
    """
    value = client.get(key)
    return None if value is None else model.model_validate_json(value)
