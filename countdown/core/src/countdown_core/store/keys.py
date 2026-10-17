"""Every Redis key and channel name the providers, renderers and broker share.

Everything is prefixed with `v1:`, so a later, incompatible layout can live beside this one while things move over.

Subscriptions (written by the broker, read by the providers):

- `v1:sub:<source>`: a plain Redis set of the ids some screen wants from `source` (one of SOURCES), replaced
  atomically whenever a config is saved. A missing key is an empty set.
- `v1:sub:changed`: a pub/sub channel the broker publishes to (empty payload) after replacing subscriptions, so the
  providers needn't wait for their next tick.

Data (written by the providers with a TTL, read by the renderers): one key per thing, holding the JSON of a
countdown_core.store.models model. A missing key means its provider is down (or hasn't got to it yet).
"""

PREFIX = "v1"

# The subscription sources, as their `v1:sub:<source>` sets are named. A provider's `name` is one of these.
TFL = "tfl"
ENTUR = "entur"
WEATHER = "weather"
GLOW = "glow"
SPOTIFY = "spotify"
SOURCES = (TFL, ENTUR, WEATHER, GLOW, SPOTIFY)

SUB_CHANGED = f"{PREFIX}:sub:changed"


def subscription(source: str) -> str:
    """Return the set of ids wanted from `source`, e.g. `v1:sub:tfl` (TfL NaPTAN stop ids)."""
    return f"{PREFIX}:sub:{source}"


def departures(stop_id: str) -> str:
    """Return the key of a stop's departures, e.g. `v1:departures:490000123W` or `v1:departures:NSR:StopPlace:58366`.

    TfL and Entur share this namespace (their ids never clash), so the transit panel needn't know the country.
    """
    return f"{PREFIX}:departures:{stop_id}"


def weather(location: str) -> str:
    """Return the key of a place's weather, by the broker's location key, e.g. `v1:weather:london-gb-2643743`."""
    return f"{PREFIX}:weather:{location}"


def energy(device_id: str) -> str:
    """Return the key of a Glow device's energy use."""
    return f"{PREFIX}:energy:{device_id}"


def spotify(device_id: str) -> str:
    """Return the key of what a device's Spotify account is playing."""
    return f"{PREFIX}:spotify:{device_id}"
