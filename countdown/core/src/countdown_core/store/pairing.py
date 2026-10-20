"""A screen's live pairing code in the store, which auth-broker writes, for the render to draw.

The broker's copy of a screen's config (`v1:config:<device id>`, store.config) never carries a pairing code, and
there's none at all while the device has a code to show. The render reads only the store, so while a code is live
the broker writes `v1:pairing:<device id>` (keys.pairing): the device's config as the contract's ConfigResponse, with
its `pairing_code` set, and queues a render of the screen. So the pairing
screen is drawn in the screen's own size and language even with no config in the store. Its TTL is the code's own
expiry (ten minutes), and the broker deletes it sooner when the code is used, or the device paired, unpaired or
forgotten.
The store contract's pairing_key.json (countdown_api.fixtures) pins the shape, which auth-broker's tests read too.

Plaintext, by decision: any Redis reader on the host can see a live code. Nothing here logs one.
"""

from typing import TYPE_CHECKING

from countdown_api.models import ConfigResponse
from countdown_core.store import keys, reader

if TYPE_CHECKING:
    import redis


def read(client: redis.Redis, device_id: str) -> ConfigResponse | None:
    """Return the device's config with the code it's to show, or None if it has no live code.

    A stored config without a code is no code. Raises RedisError if Redis can't be asked, and pydantic's
    ValidationError if what's there isn't a config the contract allows.
    """
    stored = reader.read(client, keys.pairing(device_id), ConfigResponse)
    return stored if stored is not None and stored.pairing_code else None
