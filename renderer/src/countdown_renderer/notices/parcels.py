"""Parcels on their way: the owner's Parcel deliveries, from the store (written by the parcel provider).

The renderer never asks Parcel itself, nor holds an API key; the provider does, and normalises Parcel's status codes to
the contract's ParcelStatus, so the wording (countdown_shared's: notices/wording/parcels.py) only sees those.
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource
from countdown_shared.notices.wording.parcels import parcel_notices
from countdown_shared.store import parcels, reader

if TYPE_CHECKING:
    from datetime import datetime


class StoredParcelsSource(NoticeSource):
    """The parcels the screen's settings show, from the store.

    That's `v1:parcels:<device id>`, read each time the board asks: so it says what the store has now, and nothing once
    the key has lapsed.
    """

    name = "Parcels"

    def __init__(self, device_id: str) -> None:
        """Read the device `device_id`'s parcels from the store (no HTTP, so no session)."""
        self.device_id = device_id

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Return the notices for what's stored.

        Raises NoStoredParcelsError if there's none, so the board leaves them out, RedisError if it can't ask, and
        reader.StoreNotConfiguredError if the renderer has no store.
        """
        return parcel_notices(parcels.read(reader.require_store(), self.device_id), now)
