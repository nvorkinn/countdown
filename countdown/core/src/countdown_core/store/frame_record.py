"""The screen's latest frame in the store, `v1:frame:<device id>` (keys.frame), which the broker serves the screen.

Frames reach the screen through Redis alone: the render writes each new picture here, and auth-broker answers the
screen's polls from it (its `services/frame_record.py`). It's one latest value per screen, like the providers' keys, not
a queue.

The record is a Redis hash, so the broker can read its small fields without the frame (and answer a screen that has
the picture already with a 304):

- `frame`: the panel's 48,000 bytes (800x480, a bit a pixel), as the screen takes them. Raw, not base64, which
  would take a third more of Redis's 128 MB; so it's written and read with a client that doesn't decode values
  (reader.binary_store()).
- `etag`: frame_etag(frame), the 16 hex digits the broker sends as the frame's ETag, for this copy and its own alike;
  so a screen showing the picture from either gets a 304 from the other.
- `rendered_at`: when this picture was first written, ISO 8601 in UTC to the millisecond, e.g.
  `2026-10-18T12:00:00.000+00:00`. It doesn't move while the picture stays the same: when a screen last had a good
  render is for the metrics, not the record.

A render writes the record only when its frame's hash isn't the record's `etag`: an unchanged picture costs one small
read and no write. The record has a TTL of TTL_S, a day, which a skipped write doesn't renew:

- Under the production Redis's `maxmemory-policy volatile-lru`, only keys with a TTL can be evicted. At 48 KB a frame
  is the store's biggest value, and the record of a screen nobody polls is the first that should go if Redis fills.
- A record nothing renews any more (its device forgotten, or out of `v1:render:screens`) goes by itself.
- A picture that stays the same for a whole day is written again by the first render after its record lapsed, within
  the minute. Until then, as whenever there's no record (expired, evicted, Redis restarted), the broker has nothing new
  to serve, and the screen keeps the picture it has.

`tests/fixtures/frame_record.json` (with the frame beside it, `frame_record.bin`) pins the record, the same files as
auth-broker's tests read.
"""

from datetime import UTC, datetime
from hashlib import blake2b
from typing import TYPE_CHECKING

from countdown_core.store import keys

if TYPE_CHECKING:
    import redis

# How long a record lasts after it's written: a day. See the module's docstring.
TTL_S = 24 * 60 * 60

# The hash's fields.
FRAME = "frame"
ETAG = "etag"
RENDERED_AT = "rendered_at"


def now() -> datetime:
    """Return when a record is written: now. The tests replace it to freeze the clock."""
    return datetime.now(UTC)


def frame_etag(frame: bytes) -> str:
    """Return the frame's hash, as the broker makes the ETag it serves the screen the frame with.

    That's an 8-byte BLAKE2b, in hex (auth-broker's `routes/frames.py`). Two frames with the same hash are the same
    picture, so a render whose frame hashes like the record's has nothing new to write.
    """
    return blake2b(frame, digest_size=8).hexdigest()


def write_if_changed(client: redis.Redis, device_id: str, frame: bytes) -> bool:
    """Make `frame` the screen's record, unless the record holds that picture already; return whether it was written.

    `client` may decode values or not: this reads only the `etag`, which is ASCII. The record is replaced whole, its
    fields and TTL in one transaction, so the broker never reads half of one. Raises RedisError if Redis can't be asked.
    """
    key = keys.frame(device_id)
    etag = frame_etag(frame)
    stored = client.hget(key, ETAG)
    if isinstance(stored, bytes):
        stored = stored.decode("ascii", errors="replace")
    if stored == etag:
        return False
    rendered_at = now().astimezone(UTC).isoformat(timespec="milliseconds")
    with client.pipeline(transaction=True) as pipe:
        pipe.delete(key)
        pipe.hset(key, mapping={FRAME: frame, ETAG: etag, RENDERED_AT: rendered_at})
        pipe.expire(key, TTL_S)
        pipe.execute()
    return True
