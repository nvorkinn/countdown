"""A screen's live pairing code in the store, which auth-broker writes, for a render in the pool.

The broker's copy of a screen's config (`v1:config:<device id>`, store.config) never carries a pairing code, and
there's none at all while the device has a code to show: today's per-screen renderer then asks GET /api/config, which
gives it the code. A render in the pool reads only the store (docs/stateless-renderers.md, "The pairing screen"), so
while a code is live the broker also writes `v1:pairing:<device id>` (keys.pairing): exactly what GET /api/config
answers the device then, the contract's ConfigResponse, with its `pairing_code` set. So the pairing screen is drawn in
the screen's own size and language even with no config in the store. Its TTL is the code's own expiry (ten minutes),
and the broker deletes it sooner when the code is used, or the device paired, unpaired or forgotten.
tests/fixtures/pairing_key.json pins the shape, the same file as auth-broker's tests read.

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
