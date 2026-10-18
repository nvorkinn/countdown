"""Every Redis key and channel name the providers, renderers and broker share.

Everything is prefixed with `v1:`, so a later, incompatible layout can live beside this one while things move over.

Subscriptions (written by the broker, read by the providers):

- `v1:sub:<source>`: a plain Redis set of the ids some screen wants from `source` (one of SOURCES), replaced
  atomically whenever a config is saved. A missing key is an empty set.
- `v1:sub:changed`: a pub/sub channel the broker publishes to (empty payload) after replacing subscriptions, so the
  providers needn't wait for their next tick.

Configs (written by the broker, read by the renderers): `v1:config:<device id>` holds what GET /api/config answers the
device (the contract's ConfigResponse, without its API keys or a pairing code), with no TTL: the broker rewrites it from
its database on every config save and as it starts. A missing key means the renderer asks GET /api/config instead (an
older broker, or a pairing code to show). `v1:config:changed` is the channel the broker publishes a device's id on after
changing its key. See `config`.

Data (written by the providers with a TTL, read by the renderers): one key per thing, holding the JSON of a
countdown_core.store.models model. A missing key means its provider is down (or hasn't got to it yet).

Pace (written by the providers with a short TTL, read by the renderers): `v1:pace:<source>:<id>` holds the seconds a
provider wants the screen to refresh at while what it fetched for `id` is bursty (a release running). It lapses by
itself, so a provider that stops renewing it, or dies, lets the screen go back to its usual pace. See `pace`.

Images (written by auth-broker's music provider with a short TTL, read by the renderers): `v1:art:<sha1 of its URL>`
holds an image's own bytes, not JSON, one key per image however many screens show it. See `album_art`.
"""

import hashlib

PREFIX = "v1"

# The subscription sources, as their `v1:sub:<source>` sets are named. A provider's `name` is one of these.
TFL = "tfl"
ENTUR = "entur"
WEATHER = "weather"
GLOW = "glow"
SPOTIFY = "spotify"
DEVELOPER = "developer"
# The notice board's place: every screen's postcode in the broker's canonical form ("SW1A 1AA", or "0150" for a
# Norwegian one, which is four digits), for the providers of notices about a place (floods, weather warnings, roads).
POSTCODE = "postcode"
SOURCES = (TFL, ENTUR, WEATHER, GLOW, SPOTIFY, DEVELOPER, POSTCODE)

SUB_CHANGED = f"{PREFIX}:sub:changed"


def subscription(source: str) -> str:
    """Return the set of ids wanted from `source`, e.g. `v1:sub:tfl` (TfL NaPTAN stop ids)."""
    return f"{PREFIX}:sub:{source}"


# The channel the broker publishes a device's id on after changing its config in the store.
CONFIG_CHANGED = f"{PREFIX}:config:changed"


def config(device_id: str) -> str:
    """Return the key of a device's config, e.g. `v1:config:a1b2c3d4e5f6`.

    Written by auth-broker from its database; holds a countdown_api.models.ConfigResponse, read by store.config.
    """
    return f"{PREFIX}:config:{device_id}"


def place(postcode: str) -> str:
    """Return the id a key uses for a postcode from `v1:sub:postcode`: without its space, e.g. `SW1A1AA`, `0150`."""
    return postcode.replace(" ", "").upper()


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
    """Return the key of what a device's Spotify and Apple Music are playing, e.g. `v1:spotify:<device id>`.

    Written by auth-broker's music provider; holds a countdown_core.store.music.StoredPlayer.
    """
    return f"{PREFIX}:spotify:{device_id}"


def spotify_top(device_id: str) -> str:
    """Return the key of a device's Spotify and Apple Music top tracks and artists, e.g. `v1:spotify-top:<device id>`.

    Apart from the player's key because it's fetched far less often (every half hour) and kept far longer. Written by
    auth-broker's music provider; holds a countdown_core.store.music.StoredTop.
    """
    return f"{PREFIX}:spotify-top:{device_id}"


def album_art(url: str) -> str:
    """Return the key of an album's cover, by the image's URL as the music service gave it: `v1:art:<sha1 of the URL>`.

    By URL, not device, so every screen showing the same album reads one copy. Holds the image file's raw bytes (a
    JPEG, as the service serves it; read with reader.binary_store(), as reader.store() decodes values as text), with a
    TTL of a few minutes that auth-broker's music provider renews every minute a `spotify` key still shows it. Which of
    an album's sizes is stored: countdown_core.store.music.art_image.
    """
    return f"{PREFIX}:art:{hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()}"


def developer(device_id: str) -> str:
    """Return the key of a device's developer panel, e.g. `v1:developer:<device id>`.

    Written by auth-broker's developer provider; holds a countdown_core.store.developer.StoredDeveloper.
    """
    return f"{PREFIX}:developer:{device_id}"


def pace(source: str, item_id: str) -> str:
    """Return the key of the pace `source` wants while `item_id` is bursty, e.g. `v1:pace:developer:<device id>`.

    An integer, in seconds, with a TTL of about a minute that the provider renews each cycle it still wants it.
    """
    return f"{PREFIX}:pace:{source}:{item_id}"


# Notices (the notice board's sources), one key per source and what it's fetched for. Keys by place use `place`.


def met_office(postcode: str) -> str:
    """Return the key of the Met Office's weather warnings for a UK postcode, e.g. `v1:notices:met-office:SE173LL`.

    Written by provider-met-office; holds a countdown_core.store.met_office.StoredMetOfficeWarnings: the warnings feed
    of the Met Office region the postcode is in, which the renderer turns into notices.
    """
    return f"{PREFIX}:notices:met-office:{place(postcode)}"


def bank_holidays() -> str:
    """Return the key of gov.uk's bank holidays, `v1:notices:bank-holidays`: one for all of the UK, every division's.

    Written by the bank holidays provider with no subscription, as every UK board reads it; holds a
    countdown_core.store.bank_holidays.StoredBankHolidays.
    """
    return f"{PREFIX}:notices:bank-holidays"


def entur_situations(stop_id: str) -> str:
    """Return the key of the disruptions at an Entur stop, by its id from `v1:sub:entur`.

    For example `v1:notices:entur:NSR:StopPlace:58381` or `v1:notices:entur:NSR:Quay:7333`: Entur's situations on the
    stop and on the lines leaving it. Written by provider-entur-situations; holds a
    countdown_core.store.situations.StoredSituations.
    """
    return f"{PREFIX}:notices:entur:{stop_id}"


def tfl_line_status(stop_id: str) -> str:
    """Return the key of the status of the lines serving a TfL stop, e.g. `v1:notices:tfl-lines:490000123W`.

    Written by the TfL notices provider, which asks TfL about every subscribed stop's lines at once; holds a
    countdown_core.store.tfl_notices.StoredLineStatus.
    """
    return f"{PREFIX}:notices:tfl-lines:{stop_id}"


def tfl_stations(stop_id: str) -> str:
    """Return the key of what's wrong at a TfL stop (lifts, entrances...), e.g. `v1:notices:tfl-stations:940GZZLUKNG`.

    Written by the TfL notices provider; holds a countdown_core.store.tfl_notices.StoredStationDisruptions.
    """
    return f"{PREFIX}:notices:tfl-stations:{stop_id}"


def tfl_roads(postcode: str) -> str:
    """Return the key of the road disruptions near a postcode's place, e.g. `v1:notices:tfl-roads:SE173LL`.

    Written by the TfL notices provider, for every UK postcode in `v1:sub:postcode`; holds a
    countdown_core.store.tfl_notices.StoredRoadDisruptions.
    """
    return f"{PREFIX}:notices:tfl-roads:{place(postcode)}"


def floods(postcode: str) -> str:
    """Return the key of the Environment Agency's flood warnings near a UK postcode, e.g. `v1:notices:floods:SE173LL`.

    Written by provider-floods; holds a countdown_core.store.floods.StoredFloods.
    """
    return f"{PREFIX}:notices:floods:{place(postcode)}"


def metalerts() -> str:
    """Return the key of MET Norway's current weather warnings, for all of Norway: `v1:notices:metalerts`.

    One key, not one per place: MetAlerts answers for the whole country at once, and each notice board picks out the
    warnings whose area covers its postcode. Written by provider-metalerts; holds a
    countdown_core.store.metalerts.StoredMetAlerts.
    """
    return f"{PREFIX}:notices:metalerts"
