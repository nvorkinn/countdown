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
  bytes, not JSON), `developer` and `parcels`.
- Pace (written by any provider, with a short TTL, read by the broker): `pace`.
- Frames (written by the render pool, read by countdown-server): `frame`, a hash. See store.frame_record.
- This side's providers' data (written by the providers with a TTL, read by the renderers and countdown-server): one
  key per thing, holding the JSON of a countdown_shared.store model; a missing key means its provider is down (or
  hasn't got to it yet). Departures, by stop (`departures`, TfL's and Entur's alike); weather, by weather key
  (`weather`); and the notice board's TfL line status and station disruptions by stop (`tfl_line_status`,
  `tfl_stations`), TfL road disruptions and Met Office warnings by postcode (`tfl_roads`, `met_office`, by `place`),
  gov.uk's bank holidays (BANK_HOLIDAYS), every Tube line's status (TFL_NETWORK) and Entur's situations at a stop
  (`entur_situations`). Each has its TTL beside it (DEPARTURES_TTL_S, WEATHER_TTL_S...).
- The weather panel's backgrounds (written by the background provider, read by countdown-server): a place's day of
  them by weather key (`background`) and each picture's own bytes by its SHA-256 (`background_image`, not JSON), for
  the places in `v1:sub:background` (BACKGROUND), each with what the broker says of it beside it (`background_place`).
- The render queue's keys the broker writes too: RENDER_JOBS, RENDER_SCREENS and `render_queued`. See
  store.render_queue.

Defined here are the keys only this repository writes and reads: where a postcode is (`location`, LOCATION_CHANGED), the
notices countdown-server doesn't read (`floods`, `metalerts`), a TfL stop's coming timetabled
departures (`timetable`), and the render pool's own lease and follow-up flag.
"""

from countdown_api.keys import (
    ALBUM_ART_TTL_S,
    BACKGROUND,
    BACKGROUND_IMAGE_TTL_S,
    BACKGROUND_TTL_S,
    BANK_HOLIDAYS,
    BANK_HOLIDAYS_TTL_S,
    DEPARTURES_TTL_S,
    DEVELOPER,
    DEVELOPER_TTL_S,
    ENERGY_TTL_S,
    ENTUR,
    ENTUR_SITUATIONS_TTL_S,
    FRAME_TTL_S,
    GLOW,
    MET_OFFICE_TTL_S,
    PACE_TTL_S,
    PAIRING_TTL_S,
    PARCELS_TTL_S,
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
    TFL_LINE_STATUS_TTL_S,
    TFL_NETWORK,
    TFL_NETWORK_TTL_S,
    TFL_ROADS_TTL_S,
    TFL_STATIONS_TTL_S,
    WEATHER,
    WEATHER_TTL_S,
    album_art,
    background,
    background_image,
    background_place,
    config,
    departures,
    developer,
    energy,
    entur_situations,
    frame,
    met_office,
    pace,
    pairing,
    parcels,
    render_queued,
    spotify,
    spotify_top,
    subscription,
    tfl_line_status,
    tfl_roads,
    tfl_stations,
    weather,
)

# The keys that cross to countdown-server are the store contract's (countdown-api's store.yaml: countdown_api.keys), and
# are re-exported here so everything here names every key one way. Only the keys this repository alone writes and reads
# are defined below.
__all__ = [
    "ALBUM_ART_TTL_S",
    "BACKGROUND",
    "BACKGROUND_IMAGE_TTL_S",
    "BACKGROUND_TTL_S",
    "BANK_HOLIDAYS",
    "BANK_HOLIDAYS_TTL_S",
    "DEPARTURES_TTL_S",
    "DEVELOPER",
    "DEVELOPER_TTL_S",
    "ENERGY_TTL_S",
    "ENTUR",
    "ENTUR_SITUATIONS_TTL_S",
    "FRAME_TTL_S",
    "GLOW",
    "LOCATION_CHANGED",
    "MET_OFFICE_TTL_S",
    "NOTICE_SHORT_FAILED_TTL_S",
    "NOTICE_SHORT_TTL_S",
    "PACE_TTL_S",
    "PAIRING_TTL_S",
    "PARCELS_TTL_S",
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
    "TFL_LINE_STATUS_TTL_S",
    "TFL_NETWORK",
    "TFL_NETWORK_TTL_S",
    "TFL_ROADS_TTL_S",
    "TFL_STATIONS_TTL_S",
    "TIMETABLE_TTL_S",
    "WEATHER",
    "WEATHER_TTL_S",
    "album_art",
    "background",
    "background_image",
    "background_place",
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
    "notice_short",
    "pace",
    "pairing",
    "parcels",
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
    "timetable",
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


# The notice board's sources only this side reads, one key per source and what it's fetched for (the TfL, Met Office
# and bank holidays keys and Entur's situations are the contract's). Keys by place use `place`.


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


def notice_short(digest: str) -> str:
    """Return the key of a notice text's short form, by the text's hash, e.g. `v1:notice-short:8c36...c3a9`.

    The hash is countdown_shared.notices.shortening.digest's (a SHA-256, in hex): of a provider's own prose (TfL's,
    Entur's), which a model shortened to a title and a line. Written by the provider that fetched the text
    (countdown_providers.shortening), once for however many screens show it; holds a
    countdown_shared.notices.shortening.StoredShort. Read by whoever words the notice.
    """
    return f"{PREFIX}:notice-short:{digest}"


# A week, renewed each cycle its text is still being fetched: a notice that comes back every weekend is asked about
# once.
NOTICE_SHORT_TTL_S = 7 * 24 * 60 * 60
# An hour for a text the model gave no short form (it refused, or its answer wasn't fit to show): not asked about again
# every cycle, but tried again the same day.
NOTICE_SHORT_FAILED_TTL_S = 60 * 60


def timetable(stop_id: str) -> str:
    """Return the key of a TfL stop's timetabled departures, e.g. `v1:timetable:940GZZLUKNG`.

    Written by the TfL timetable provider (countdown_providers.tfl_timetable) for every stop in `v1:sub:tfl`, with a
    TTL of TIMETABLE_TTL_S; holds a countdown_shared.store.timetable.StoredTimetable. Read by the TfL provider, which
    adds a line's next timetabled departures to the stop's departures, marked `scheduled`, while TfL predicts nothing
    for that line there (the Tube overnight).
    """
    return f"{PREFIX}:timetable:{stop_id}"


# A few of the timetable provider's cycles (every 15 minutes): one or two failing leave the first trains showing.
TIMETABLE_TTL_S = 2 * 60 * 60
