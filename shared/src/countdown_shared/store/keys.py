"""Every Redis key and channel name the providers, renderers and broker share.

Everything is prefixed with `v1:`, so a later, incompatible layout can live beside this one while things move over.

The keys that cross between this side and countdown-server are the store contract's: countdown-api's store.yaml, as
countdown_api.keys (its builders say what each holds, its TTL, and who writes and reads it), re-exported here. They are:

- Subscriptions (written by the broker, read by the providers): `v1:sub:<source>` (`subscription`), a set of the ids
  some screen wants from `source` (one of SOURCES), replaced atomically whenever a config is saved, and the
  `v1:sub:changed` channel (SUB_CHANGED) the broker nudges after.
- Configs and pairing codes (written by the broker, read by the render pool): `v1:config:<device id>` (`config`), with
  no TTL, and `v1:pairing:<device id>` (`pairing`), while the device has a code to show, with the code's own TTL. See
  store.config and store.pairing.
- The broker's providers' data (read by the renderers): `energy`, `spotify`, `spotify_top`, `album_art` (an image's own
  bytes, not JSON) and `developer`.
- Pace (written by any provider, with a short TTL, read by the broker): `pace`.
- Frames (written by the render pool, read by countdown-server): `frame`, a hash. See store.frame_record.
- The render queue's keys the broker writes too: RENDER_JOBS, RENDER_SCREENS and `render_queued`. See
  store.render_queue.

Defined here are the keys only this repository writes and reads. Data (written by the providers with a TTL, read by the
renderers): one key per thing, holding the JSON of a countdown_shared.store.models model; a missing key means its
provider is down (or hasn't got to it yet). And the render pool's own lease and follow-up flag.
"""

from countdown_api.keys import (
    ALBUM_ART_TTL_S,
    DEVELOPER,
    DEVELOPER_TTL_S,
    ENERGY_TTL_S,
    ENTUR,
    FRAME_TTL_S,
    GLOW,
    PACE_TTL_S,
    PAIRING_TTL_S,
    POSTCODE,
    PREFIX,
    RENDER_JOBS,
    RENDER_QUEUED_TTL_MS,
    RENDER_SCREENS,
    SOURCES,
    SPOTIFY,
    SPOTIFY_TOP_TTL_S,
    SPOTIFY_TTL_S,
    SUB_CHANGED,
    TFL,
    WEATHER,
    album_art,
    config,
    developer,
    energy,
    frame,
    pace,
    pairing,
    render_queued,
    spotify,
    spotify_top,
    subscription,
)

# The keys that cross to countdown-server are the store contract's (countdown-api's store.yaml: countdown_api.keys), and
# are re-exported here so everything here names every key one way. Only the keys this repository alone writes and reads
# are defined below.
__all__ = [
    "ALBUM_ART_TTL_S",
    "DEVELOPER",
    "DEVELOPER_TTL_S",
    "ENERGY_TTL_S",
    "ENTUR",
    "FRAME_TTL_S",
    "GLOW",
    "LOCATION_CHANGED",
    "PACE_TTL_S",
    "PAIRING_TTL_S",
    "POSTCODE",
    "PREFIX",
    "RENDER_JOBS",
    "RENDER_QUEUED_TTL_MS",
    "RENDER_SCREENS",
    "SOURCES",
    "SPOTIFY",
    "SPOTIFY_TOP_TTL_S",
    "SPOTIFY_TTL_S",
    "SUB_CHANGED",
    "TFL",
    "WEATHER",
    "album_art",
    "bank_holidays",
    "config",
    "departures",
    "developer",
    "energy",
    "entur_situations",
    "floods",
    "frame",
    "location",
    "met_office",
    "metalerts",
    "pace",
    "pairing",
    "place",
    "render_again",
    "render_lease",
    "render_queued",
    "spotify",
    "spotify_top",
    "subscription",
    "tfl_line_status",
    "tfl_roads",
    "tfl_stations",
    "weather",
]


def place(postcode: str) -> str:
    """Return the id a key uses for a postcode from `v1:sub:postcode`: without its space, e.g. `SW1A1AA`, `0150`."""
    return postcode.replace(" ", "").upper()


def location(postcode: str) -> str:
    """Return the key of where a postcode from `v1:sub:postcode` is, e.g. `v1:location:SE173LL`, `v1:location:0150`.

    Written by provider-locations, the one process that looks postcodes up (postcodes.io, Kartverket), with a TTL of
    days; holds a countdown_shared.store.location.StoredLocation, which says so when the postcode doesn't exist. The
    notice board, and the providers of notices about a place (Met Office, floods, TfL roads), read it rather than look
    a postcode up themselves; so does the weather provider, for a weather key that's a typed postcode's slug
    (store.location.weather_postcode), which provider-locations places too.
    """
    return f"{PREFIX}:location:{place(postcode)}"


# The channel provider-locations publishes on (empty payload) after writing a postcode's place that's new or has
# moved, so the providers that read `v1:location:` keys run a cycle then rather than at their next tick.
LOCATION_CHANGED = f"{PREFIX}:location:changed"


def departures(stop_id: str) -> str:
    """Return the key of a stop's departures, e.g. `v1:departures:490000123W` or `v1:departures:NSR:StopPlace:58366`.

    TfL and Entur share this namespace (their ids never clash), so the transit panel needn't know the country.
    """
    return f"{PREFIX}:departures:{stop_id}"


def weather(location: str) -> str:
    """Return the key of a place's weather, by the broker's location key, e.g. `v1:weather:london-gb-2643743`."""
    return f"{PREFIX}:weather:{location}"


# The render pool's queue (store.render_queue): RENDER_JOBS, RENDER_SCREENS and render_queued are the contract's, as
# countdown-server queues jobs and lists the screens too; the lease and the follow-up flag are the pool's own.


def render_lease(device_id: str) -> str:
    """Return the lease on the screen's render, e.g. `v1:render:lease:a1b2c3d4e5f6`: the token of the worker drawing it.

    With a TTL longer than the render's deadline, so a worker that dies mid-render frees the screen by itself.
    """
    return f"{PREFIX}:render:lease:{device_id}"


def render_again(device_id: str) -> str:
    """Return the flag that a job came for the screen while its render was in flight, e.g. `v1:render:again:<id>`.

    The release queues one follow-up render for it, however many jobs came.
    """
    return f"{PREFIX}:render:again:{device_id}"


# Notices (the notice board's sources), one key per source and what it's fetched for. Keys by place use `place`.


def met_office(postcode: str) -> str:
    """Return the key of the Met Office's weather warnings for a UK postcode, e.g. `v1:notices:met-office:SE173LL`.

    Written by provider-met-office; holds a countdown_shared.store.met_office.StoredMetOfficeWarnings: the warnings feed
    of the Met Office region the postcode is in, which the renderer turns into notices.
    """
    return f"{PREFIX}:notices:met-office:{place(postcode)}"


def bank_holidays() -> str:
    """Return the key of gov.uk's bank holidays, `v1:notices:bank-holidays`: one for all of the UK, every division's.

    Written by the bank holidays provider with no subscription, as every UK board reads it; holds a
    countdown_shared.store.bank_holidays.StoredBankHolidays.
    """
    return f"{PREFIX}:notices:bank-holidays"


def entur_situations(stop_id: str) -> str:
    """Return the key of the disruptions at an Entur stop, by its id from `v1:sub:entur`.

    For example `v1:notices:entur:NSR:StopPlace:58381` or `v1:notices:entur:NSR:Quay:7333`: Entur's situations on the
    stop and on the lines leaving it. Written by provider-entur-situations; holds a
    countdown_shared.store.situations.StoredSituations.
    """
    return f"{PREFIX}:notices:entur:{stop_id}"


def tfl_line_status(stop_id: str) -> str:
    """Return the key of the status of the lines serving a TfL stop, e.g. `v1:notices:tfl-lines:490000123W`.

    Written by the TfL notices provider, which asks TfL about every subscribed stop's lines at once; holds a
    countdown_shared.store.tfl_notices.StoredLineStatus.
    """
    return f"{PREFIX}:notices:tfl-lines:{stop_id}"


def tfl_stations(stop_id: str) -> str:
    """Return the key of what's wrong at a TfL stop (lifts, entrances...), e.g. `v1:notices:tfl-stations:940GZZLUKNG`.

    Written by the TfL notices provider; holds a countdown_shared.store.tfl_notices.StoredStationDisruptions.
    """
    return f"{PREFIX}:notices:tfl-stations:{stop_id}"


def tfl_roads(postcode: str) -> str:
    """Return the key of the road disruptions near a postcode's place, e.g. `v1:notices:tfl-roads:SE173LL`.

    Written by the TfL notices provider, for every UK postcode in `v1:sub:postcode`; holds a
    countdown_shared.store.tfl_notices.StoredRoadDisruptions.
    """
    return f"{PREFIX}:notices:tfl-roads:{place(postcode)}"


def floods(postcode: str) -> str:
    """Return the key of the Environment Agency's flood warnings near a UK postcode, e.g. `v1:notices:floods:SE173LL`.

    Written by provider-floods; holds a countdown_shared.store.floods.StoredFloods.
    """
    return f"{PREFIX}:notices:floods:{place(postcode)}"


def metalerts() -> str:
    """Return the key of MET Norway's current weather warnings, for all of Norway: `v1:notices:metalerts`.

    One key, not one per place: MetAlerts answers for the whole country at once, and each notice board picks out the
    warnings whose area covers its postcode. Written by provider-metalerts; holds a
    countdown_shared.store.metalerts.StoredMetAlerts.
    """
    return f"{PREFIX}:notices:metalerts"
