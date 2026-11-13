"""The screen's config in the store, which countdown-server writes.

The broker keeps its database as the source of truth and writes each screen's config to `v1:config:<device id>`
(keys.config): the contract's ConfigResponse, less its secrets (the weather API key empty, no pairing code), with no
TTL. It rewrites it on every config save and as it starts, and queues a render of the screen (store.render_queue). The
render reads it here (read() gives None for a device with nothing to draw: not paired, or with a pairing code to show,
which is store.pairing's).

The value is self-contained: a fresh process can draw the screen from it and the providers' keys alone.
"""

from typing import TYPE_CHECKING

from countdown_api.models import ConfigResponse
from countdown_shared.store import keys, reader

if TYPE_CHECKING:
    import redis


def read(client: redis.Redis, device_id: str) -> ConfigResponse | None:
    """Return the device's config as the broker stored it, or None if there's none for it.

    Raises RedisError if Redis can't be asked, and pydantic's ValidationError if what's there isn't a config the
    contract allows (a broker on a newer contract that tightened something).
    """
    return reader.read(client, keys.config(device_id), ConfigResponse)


def read_text(client: redis.Redis, device_id: str) -> str | None:
    """Return the device's config as the text the broker stored, or None if there's none for it.

    For a render, which parses it (parse) and says which text its frame was drawn from (frame_record.write_if_changed).
    Raises RedisError if Redis can't be asked.
    """
    value = client.get(keys.config(device_id))
    return value.decode("utf-8") if isinstance(value, bytes) else value


def parse(text: str) -> ConfigResponse:
    """Return the config in `text` (read_text). Raises pydantic's ValidationError if it isn't one, as read() does."""
    return ConfigResponse.model_validate_json(text)
