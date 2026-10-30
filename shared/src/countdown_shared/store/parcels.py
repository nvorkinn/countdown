"""What countdown-server's parcel provider stores for the parcel notices, and the renderer reads back.

The provider holds the owner's Parcel API key and asks Parcel; the renderer only reads this. The value is the store
contract's ParcelList (countdown_api.models), whose parcels have Parcel's status codes already normalised to a
ParcelStatus, so nothing here sees a raw code. The contract's parcels.json (countdown_api.fixtures) is an example,
which both repositories' tests read, so neither side can drift alone.
"""

from typing import TYPE_CHECKING

from countdown_api.models import ParcelList
from countdown_shared.store import keys, reader

if TYPE_CHECKING:
    import redis


class NoStoredParcelsError(reader.NoStoredDataError):
    """The store has nothing for the device: the parcel provider is down (its key expired), or hasn't fetched yet."""


def read(store: redis.Redis, device_id: str) -> ParcelList:
    """Read the device's parcels, as its notices do each time the board is drawn.

    Raises NoStoredParcelsError if there are none, and RedisError if the store can't be asked.
    """
    stored = reader.read(store, keys.parcels(device_id), ParcelList)
    if stored is None:
        msg = f"No parcels in the store for device {device_id}"
        raise NoStoredParcelsError(msg)
    return stored
