"""The renderer's side of the store: reading what the providers wrote.

Every renderer runs on the server and reads the store: the orchestrator passes it REDIS_URL (and REDIS_PASSWORD, if
Redis wants one). The departures, weather, energy, music and developer clients read only the store (require_store()); a
renderer started without REDIS_URL can't draw them, and those panels show their error state instead. Only the refresh
pace still copes without one (store()).

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


class StoreNotConfiguredError(RuntimeError):
    """The renderer was started without REDIS_URL, so it has no store to read its panels' data from."""


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
    down raises later, from each read, and the registry just keeps the last panel.
    """
    client = store()
    if client is None:
        msg = "REDIS_URL is not set: this renderer reads its panels' data from the store, and has none to read"
        raise StoreNotConfiguredError(msg)
    return client


def device_id(registration: object) -> str:
    """Return the device id a renderer's keys in the store are under; raises ValueError if `registration` has none.

    Only a renderer's registration (RendererRegistration) carries one.
    """
    found = getattr(registration, "device_id", None)
    if not found:
        msg = "This registration has no device id, which the store's keys for this device are under"
        raise ValueError(msg)
    return found


def read[M: BaseModel](client: redis.Redis, key: str, model: type[M]) -> M | None:
    """Return the model stored under `key`, or None if there's none (its provider is down or hasn't got to it yet).

    Raises if Redis can't be asked, or if what's there isn't a `model` (a provider on another version).
    """
    value = client.get(key)
    return None if value is None else model.model_validate_json(value)


def fastest_pace(client: redis.Redis, pace_keys: list[str]) -> int | None:
    """Return the shortest refresh pace, in seconds, that the providers behind `pace_keys` ask for; None if none do.

    A key is there only while its provider wants the screen faster (see keys.pace). Anything that isn't a positive
    whole number is ignored, so one provider on another version can't stall the screen.
    """
    if not pace_keys:
        return None
    paces = [int(value) for value in client.mget(pace_keys) if value is not None and value.isdigit() and int(value) > 0]
    return min(paces, default=None)
