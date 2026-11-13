"""The screen's latest frame in the store, `v1:frame:<device id>` (keys.frame), which the broker serves the screen.

Frames reach the screen through Redis alone: the render writes each new picture here, and countdown-server answers the
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

A render can also say which config it drew the frame from (`drawn_from`, the text under keys.config as it read it).
If the broker has replaced the config since, the frame is of settings the screen no longer has (hung the other way,
say, which the broker's pages would show on its side), so it isn't written (SupersededError): the save queued a render
of the new config, and that one's frame is the screen's. The check and the write are one transaction (WATCH), so a
config replaced between them fails the write too.

The store contract's `frame_record.json` (with the frame beside it, `frame_record.bin`; countdown_api.fixtures) is the
canonical record, which countdown-server's tests read too.
"""

from datetime import UTC, datetime
from hashlib import blake2b

import redis

from countdown_shared.store import keys

# How long a record lasts after it's written: a day (the store contract's). See the module's docstring.
TTL_S = keys.FRAME_TTL_S

# The hash's fields.
FRAME = "frame"
ETAG = "etag"
RENDERED_AT = "rendered_at"


def now() -> datetime:
    """Return when a record is written: now. The tests replace it to freeze the clock."""
    return datetime.now(UTC)


def frame_etag(frame: bytes) -> str:
    """Return the frame's hash, as the broker makes the ETag it serves the screen the frame with.

    That's an 8-byte BLAKE2b, in hex (countdown-server's `routes/frames.py`). Two frames with the same hash are the same
    picture, so a render whose frame hashes like the record's has nothing new to write.
    """
    return blake2b(frame, digest_size=8).hexdigest()


class SupersededError(Exception):
    """The frame was drawn from a config the broker has since replaced, so it wasn't written."""


def write_if_changed(client: redis.Redis, device_id: str, frame: bytes, *, drawn_from: str | None = None) -> bool:
    """Make `frame` the screen's record, unless the record holds that picture already; return whether it was written.

    `drawn_from` is the screen's config as the render read it from the store (keys.config's text), or None for a frame
    that isn't drawn from it (the pairing screen). A frame whose config the store no longer holds isn't written:
    SupersededError.

    `client` may decode values or not: this reads only the `etag`, which is ASCII, and the config, which is UTF-8. The
    record is replaced whole, its fields and TTL in one transaction, so the broker never reads half of one. Raises
    RedisError if Redis can't be asked.
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
        if drawn_from is not None:
            # Watched before it's read, so a config replaced after the read fails the transaction.
            config_key = keys.config(device_id)
            pipe.watch(config_key)
            current = pipe.get(config_key)
            if isinstance(current, bytes):
                current = current.decode("utf-8", errors="replace")
            if current != drawn_from:
                raise SupersededError
            pipe.multi()
        pipe.delete(key)
        pipe.hset(key, mapping={FRAME: frame, ETAG: etag, RENDERED_AT: rendered_at})
        pipe.expire(key, TTL_S)
        try:
            pipe.execute()
        except redis.WatchError:
            raise SupersededError from None
    return True
