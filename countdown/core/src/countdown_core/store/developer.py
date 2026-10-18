"""What auth-broker's developer provider stores for the developer panel, and the renderer reads back.

The provider runs in auth-broker (broker/developer_provider.py), which can't import this, so the contract is the JSON
itself: the GitHub summary and the AI plans (countdown_core.developer.models) as the broker's services/developer.py
builds them, with `fetched_at` beside it. tests/fixtures/developer.json is an example, copied into both repositories'
tests so neither side can drift alone. The broker has no route for the panel: the store is the renderer's only source.

`github` is None until the owner adds a GitHub token.
"""

from typing import TYPE_CHECKING

from countdown_core.developer.models import DeveloperSummary
from countdown_core.store import keys, reader
from countdown_core.store.models import Stored

if TYPE_CHECKING:
    import redis


class StoredDeveloper(Stored, DeveloperSummary):
    """The value of `v1:developer:<device id>` (TTL ~15 min): the developer panel's data, as of `fetched_at`."""


class NoStoredDeveloperError(reader.NoStoredDataError):
    """The store has nothing for the device: the developer provider is down (its key expired), or hasn't fetched it."""


def read(store: redis.Redis, device_id: str) -> StoredDeveloper:
    """Read the device's developer panel data, as the panel and its notices both do, each when it's drawn.

    Raises NoStoredDeveloperError if there's none, and RedisError if the store can't be asked.
    """
    stored = reader.read(store, keys.developer(device_id), StoredDeveloper)
    if stored is None:
        msg = f"No developer data in the store for device {device_id}"
        raise NoStoredDeveloperError(msg)
    return stored
