"""Nothing logged says where a screen is: no postcode, outward code, district, place or coordinates, nor a URL with one.

The logs go to the central log store, and a postcode says where someone lives. Each test drives one way a failure
(or a success) is logged, at DEBUG, with the logging set up as every process's main() sets it up, and reads every
record it made. The failures that matter most come from requests: its errors quote the URL they asked, and urllib3's
retry warnings quote it too. For the same reason nothing logged holds TfL's app key, which is in every TfL URL.
"""

import asyncio
import json
import logging
import re
import socket
from datetime import UTC, datetime

import fakeredis
import pytest
import requests
import responses
from location_factory import FakeLocations
from redis.exceptions import RedisError
from test_utils import TEST_DEVICE_ID

from countdown_core.core import http_session, log_format
from countdown_core.notices import location, norway_feed
from countdown_core.notices.location import lookup_outcode, lookup_postcode
from countdown_core.notices.models import FloodWarningsResponse
from countdown_core.notices.norway_feed import ADDRESSES_URL, lookup_norwegian_postcode
from countdown_core.store import keys
from countdown_core.store.floods import StoredFloods
from countdown_core.store.location import StoredLocation
from countdown_core.tfl import tfl_client
from countdown_core.tfl.models import Postcode
from countdown_core.tfl.tfl_client import TFL_URL, TflSource
from countdown_core.weather import open_meteo
from countdown_core.weather.open_meteo import fetch_forecast, geocode
from countdown_providers.floods import main as floods_main
from countdown_providers.floods.main import FloodsProvider, fetch_flood_areas
from countdown_providers.locations.main import LocationsProvider, WeatherPostcodesProvider
from countdown_providers.met_office import main as met_office_main
from countdown_providers.met_office.main import MetOfficeProvider
from countdown_providers.runner import ProviderRunner
from countdown_providers.tfl.main import TflProvider
from countdown_providers.tfl_notices.main import TflLineStatusProvider, TflRoadProvider, TflStationProvider
from countdown_providers.weather.main import WeatherProvider
from countdown_renderer import moment
from countdown_renderer.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
# Coordinates odd enough that finding them in a log can't be chance.
LATITUDE, LONGITUDE = 51.48765, -0.09321
SOUTHWARK = {
    "postcode": "SE17 3LL",
    "latitude": LATITUDE,
    "longitude": LONGITUDE,
    "country": "England",
    "region": "London",
    "admin_district": "Southwark",
}
DRAMMEN = {"postnummer": "3015", "kommunenavn": "DRAMMEN", "representasjonspunkt": {"lat": 59.74389, "lon": 10.20449}}
YORK = {
    "postcode": "YO1 7HH",
    "latitude": 53.96197,
    "longitude": -1.08273,
    "country": "England",
    "region": "Yorkshire and The Humber",
    "admin_district": "York",
}


def traces(postcode: str, *places: str | float) -> list[str]:
    """Return what no log may hold about `postcode`: it, without its space, its outward code, and `places`."""
    compact = postcode.replace(" ", "").upper()
    outward = postcode.split(maxsplit=1)[0].upper() if " " in postcode else compact
    return [postcode.upper(), compact, outward, *named(*places)]


def named(*places: str | float) -> list[str]:
    """Return what no log may hold about `places` (names, coordinates), as assert_nothing_says_where compares them."""
    return [str(place).upper() for place in places]


def assert_nothing_says_where(caplog, found: list[str]) -> None:
    """Assert no record (its message, its arguments, its traceback) has any of `found` in it."""
    text = caplog.text.upper()  # each record formatted, with its exc_info
    for trace in found:
        assert trace not in text, f"the logs mention {trace!r}:\n{caplog.text}"


@pytest.fixture
def production_logging(monkeypatch, caplog):
    """Set up the logging as every process's main() does (bar the JSON handler), at LOG_LEVEL=debug; undo it after."""
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setattr(log_format.logging, "basicConfig", lambda **kwargs: None)
    loggers = [logging.getLogger(name) for name in (*log_format.NOISY, *log_format.SILENCED)]
    levels = [logger.level for logger in loggers]
    log_format.configure_logging()
    caplog.set_level(logging.DEBUG)
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


@pytest.fixture
def nowhere(monkeypatch) -> str:
    """Return the base URL of a port nothing listens on, and send postcodes.io, Kartverket and Open-Meteo there."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{s.getsockname()[1]}"
    monkeypatch.setattr(location, "POSTCODES_URL", f"{base}/postcodes")
    monkeypatch.setattr(location, "OUTCODES_URL", f"{base}/outcodes")
    monkeypatch.setattr(norway_feed, "ADDRESSES_URL", f"{base}/adresser/v1/sok")
    monkeypatch.setattr(open_meteo, "GEOCODING_URL", f"{base}/v1/search")
    monkeypatch.setattr(open_meteo, "FORECAST_URL", f"{base}/v1/forecast")
    monkeypatch.setattr(floods_main, "FLOOD_AREAS_URL", f"{base}/id/floodAreas")
    return base


def retrying_session() -> requests.Session:
    """Return countdown_core's retrying session (the providers' too), retrying at once rather than backing off."""
    session = http_session.retrying_session(backoff_factor=0)
    session.trust_env = False  # straight to the closed port, not through a proxy
    return session


# --- urllib3's retries, through the real retrying session -----------------------------------------------------------


@pytest.mark.usefixtures("production_logging")
@pytest.mark.parametrize(
    ("ask", "found"),
    [
        pytest.param(lambda s: lookup_postcode(s, "SE17 3LL"), traces("SE17 3LL"), id="postcode"),
        pytest.param(lambda s: lookup_outcode(s, "SE17"), traces("SE17"), id="outward code"),
        pytest.param(lambda s: lookup_norwegian_postcode(s, "3015"), traces("3015"), id="norwegian postcode"),
        pytest.param(lambda s: geocode(s, "Little Snoring"), named("Little Snoring", "Little+Snoring"), id="place"),
        pytest.param(lambda s: fetch_forecast(s, LATITUDE, LONGITUDE), named(LATITUDE, LONGITUDE), id="coordinates"),
        pytest.param(lambda s: fetch_flood_areas(s, LATITUDE, LONGITUDE), named(LATITUDE, LONGITUDE), id="flood areas"),
    ],
)
def test_a_retried_connection_failure_logs_no_url(nowhere, caplog, ask, found):
    """urllib3 warns on each retry, quoting the URL: kept out of every process's logs (configure_logging)."""
    with pytest.raises(requests.ConnectionError):
        ask(retrying_session())

    assert not [record for record in caplog.records if record.name.startswith("urllib3")]
    assert_nothing_says_where(caplog, found)


# --- Placing a postcode: provider-locations looks it up, and the notice board reads where it is from the store -------


@pytest.mark.usefixtures("production_logging", "nowhere")
@pytest.mark.parametrize("postcode", ["SE17 3LL", "3015"], ids=["uk", "norway"])
def test_a_failed_location_lookup_logs_no_postcode(caplog, postcode):
    """postcodes.io (Kartverket for Norway) can't be reached: provider-locations', the runner's and urllib3's logs."""
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)

    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), postcode)
        return await ProviderRunner(LocationsProvider(retrying_session(), now=lambda: NOW), redis).run_cycle()

    assert asyncio.run(cycle()) == 0

    assert "Couldn't look up a postcode: ConnectionError" in caplog.messages
    assert_nothing_says_where(caplog, traces(postcode))


@pytest.fixture
def clock(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    # The moment the board draws at (core.moment), as a render sets it.
    monkeypatch.setattr(moment, "now", lambda: FrozenDatetime.now(UTC))


def put_location(store, postcode: str, place: Postcode) -> None:
    """Store where `postcode` is, as provider-locations writes it."""
    stored = StoredLocation(fetched_at=NOW, postcode=postcode, found=True, location=place)
    store.set(keys.location(postcode), stored.model_dump_json())


@pytest.mark.usefixtures("production_logging", "clock")
@pytest.mark.parametrize(
    ("place", "found"),
    [
        pytest.param(SOUTHWARK, traces("SE17 3LL", "Southwark", "London", LATITUDE, LONGITUDE), id="a region"),
        pytest.param(
            SOUTHWARK | {"region": "Ruritania", "admin_district": "Strelsau"},
            traces("SE17 3LL", "Ruritania", "Strelsau", LATITUDE, LONGITUDE),
            id="no met office region",
        ),
    ],
)
def test_a_placed_postcode_and_its_stored_sources_log_no_place(store, caplog, place, found):
    """Placed from the store, its sources read from it with nothing there: the success, and each missing key."""
    put_location(store, "SE17 3LL", Postcode.model_validate(place))
    with responses.RequestsMock():  # and nothing asked of the network
        client = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(postcode="SE17 3LL"))
        assert asyncio.run(client.update()).message == "Not available"  # every one of its keys is missing

    assert "Notice board: found the postcode's location; reading its local notices" in caplog.messages
    assert "TfL roads: No TfL road disruptions in the store for the board's postcode" in caplog.messages
    assert_nothing_says_where(caplog, found)


@pytest.mark.usefixtures("production_logging", "clock")
def test_a_placed_norwegian_postcode_logs_no_place(store, caplog):
    with responses.RequestsMock() as api:
        api.add(responses.GET, ADDRESSES_URL, json={"adresser": [DRAMMEN]})
        drammen = lookup_norwegian_postcode(requests.Session(), "3015")  # as provider-locations places it
    assert drammen is not None
    put_location(store, "3015", drammen)
    with responses.RequestsMock():
        client = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(postcode="3015", country="NO"))
        assert asyncio.run(client.update()).message == "Not available"  # every one of its keys is missing

    assert "Notice board: found the postcode's location; reading its local notices" in caplog.messages
    assert_nothing_says_where(caplog, traces("3015", "Drammen", 59.74389, 10.20449))


# --- The providers: the runner's log of a failed batch, and the weather's -------------------------------------------


class PlacingProvider:
    """Places each postcode with postcodes.io, as provider-locations does, but lets a failure fail the whole batch.

    The runner logs that: its error's text quotes postcodes.io's URL, the postcode in it.
    """

    name = keys.POSTCODE
    ttl_s = 60
    interval_s = 60

    def __init__(self, session: requests.Session) -> None:
        self.session = session

    def output_key(self, item_id: str) -> str:
        return keys.floods(item_id)

    async def fetch(self, ids: list[str]) -> dict:
        for postcode in ids:
            lookup_postcode(self.session, postcode)
        return {}


@pytest.mark.usefixtures("production_logging", "nowhere")
def test_the_runner_logs_a_failed_batch_of_postcodes_by_its_size_and_reason_alone(caplog):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)

    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), "SE17 3LL", "YO1 7HH")
        return await ProviderRunner(PlacingProvider(retrying_session()), redis).run_cycle()

    assert asyncio.run(cycle()) == 0

    assert_nothing_says_where(caplog, [*traces("SE17 3LL"), *traces("YO1 7HH")])
    [failed] = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert failed.getMessage() == "postcode: couldn't fetch 2 ids: ConnectionError"
    assert "in fetch" in caplog.text  # the traceback is kept, where it was raised: just not what it said


@pytest.mark.usefixtures("production_logging")
def test_the_runner_still_names_a_failed_batch_of_stops_by_its_first(caplog):
    class Failing:
        name = keys.TFL
        ttl_s = 60
        interval_s = 60

        def output_key(self, item_id):
            return keys.departures(item_id)

        async def fetch(self, ids):
            raise requests.ConnectionError

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    asyncio.run(redis.sadd(keys.subscription(keys.TFL), "490000123W", "490000124X"))

    asyncio.run(ProviderRunner(Failing(), redis).run_cycle())

    assert "tfl: couldn't fetch 2 ids (490000123W...): ConnectionError" in caplog.messages


@pytest.mark.usefixtures("production_logging")
def test_a_refused_redis_write_of_a_place_logs_no_key_or_value(caplog):
    """A failed pipeline's text quotes its command: the key (the postcode's place) and the value (the postcode)."""

    class Refused:
        name = keys.POSTCODE
        ttl_s = -1  # SET ... EX -1: Redis refuses it, as it would a write over its memory limit
        interval_s = 60

        def output_key(self, item_id):
            return keys.floods(item_id)

        async def fetch(self, ids):
            return {
                postcode: StoredFloods(fetched_at=NOW, postcode=postcode, floods=FloodWarningsResponse(items=[]))
                for postcode in ids
            }

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    asyncio.run(redis.sadd(keys.subscription(keys.POSTCODE), "SE17 3LL"))

    asyncio.run(ProviderRunner(Refused(), redis)._try_cycle())

    assert "postcode: couldn't reach Redis (ResponseError); trying again next tick" in caplog.messages
    assert_nothing_says_where(caplog, traces("SE17 3LL"))


@pytest.mark.usefixtures("production_logging", "nowhere")
@pytest.mark.parametrize(
    ("location_key", "known", "found"),
    [
        pytest.param(
            "sw1a-1aa", None, [*traces("SW1A 1AA", LATITUDE, LONGITUDE), "SW1A-1AA"], id="typed postcode, placed"
        ),
        pytest.param("se17", None, [*traces("SE17", LATITUDE, LONGITUDE), "SE17"], id="typed district, placed"),
        pytest.param(
            "little-snoring", None, named("little-snoring", "Little Snoring", "Little+Snoring"), id="typed place"
        ),
        pytest.param(
            "london-gb-2643743", (LATITUDE, LONGITUDE), named("London", LATITUDE, LONGITUDE), id="picked place"
        ),
    ],
)
def test_the_weather_provider_logs_a_failed_place_without_naming_it(caplog, location_key, known, found):
    """Geocoding a typed place (Open-Meteo), or the forecast at a place's coordinates (a typed postcode's, stored)."""
    located = FakeLocations(
        Postcode(postcode="SW1A 1AA", latitude=LATITUDE, longitude=LONGITUDE, country="England", region="London"),
        Postcode(postcode="SE17", latitude=LATITUDE, longitude=LONGITUDE, country="England"),
    )
    provider = WeatherProvider(retrying_session(), located.reader, now=lambda: NOW)
    if known is not None:
        provider.coordinates[location_key] = known

    assert asyncio.run(provider.fetch([location_key])) == {}

    assert caplog.messages[-1] == "Couldn't fetch the weather for a subscribed place: ConnectionError"
    assert_nothing_says_where(caplog, found)


@pytest.mark.usefixtures("production_logging", "nowhere")
def test_the_weather_provider_logs_no_typed_postcode_the_store_cant_place(caplog):
    """Not placed yet, not a StoredLocation, and not a postcode that exists: through the runner, at DEBUG."""
    located = FakeLocations(not_found=("ZZ1 1ZZ",))
    located.store.set(keys.location("YO1 7HH"), json.dumps({"postcode": "YO1 7HH", "found": True, "location": YORK}))
    provider = WeatherProvider(retrying_session(), located.reader, now=lambda: NOW)

    async def cycle():
        await located.redis.sadd(keys.subscription(keys.WEATHER), "e1-6an", "yo1-7hh", "zz1-1zz", "se17")
        return await ProviderRunner(provider, located.redis).run_cycle()

    assert asyncio.run(cycle()) == 0

    assert len(caplog.messages) >= 3
    assert_nothing_says_where(
        caplog,
        [
            *traces("E1 6AN"),
            *traces("YO1 7HH", "York", YORK["latitude"], YORK["longitude"]),
            *traces("ZZ1 1ZZ"),
            "SE17",
            "E1-6AN",
            "YO1-7HH",
            "ZZ1-1ZZ",
        ],
    )


@pytest.mark.usefixtures("production_logging", "nowhere")
@pytest.mark.parametrize("location_key", ["sw1a-1aa", "se17"], ids=["postcode", "district"])
def test_provider_locations_logs_no_weather_keys_postcode_it_couldnt_look_up(caplog, location_key):
    """provider-locations' weather part: postcodes.io can't be reached; its, the runner's and urllib3's logs."""
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    postcodes = LocationsProvider(retrying_session(), now=lambda: NOW)

    async def cycle():
        await redis.sadd(keys.subscription(keys.WEATHER), location_key, "london-gb-2643743")
        return await ProviderRunner(WeatherPostcodesProvider(postcodes), redis).run_cycle()

    assert asyncio.run(cycle()) == 0

    assert "Couldn't look up a postcode: ConnectionError" in caplog.messages
    assert_nothing_says_where(caplog, [*traces("SW1A 1AA"), "SE17", location_key.upper(), "LONDON"])


# --- The providers of notices about a place, reading where each postcode is from the store ---------------------------

PLACE_PROVIDERS = {
    # What its log says it can't fetch, and what it says the postcodes it can't place go without.
    "met office": ("Couldn't fetch a region's Met Office warnings: ConnectionError", "Met Office warnings"),
    "floods": ("Couldn't fetch the flood areas near a subscribed postcode: ConnectionError", "flood warnings"),
    "tfl roads": ("Couldn't fetch TfL's road disruptions: ConnectionError", "TfL road notices"),
}


def place_provider(kind: str, located: FakeLocations, session: requests.Session):
    """Return the provider of `kind`, reading where postcodes are from `located` and asking upstream on `session`."""
    if kind == "met office":
        return MetOfficeProvider(session, located.reader, now=lambda: NOW)
    if kind == "floods":
        floods = FloodsProvider(session, located.reader, now=lambda: NOW)
        floods.in_force = (NOW, FloodWarningsResponse())  # asked for already: then the flood areas near each place
        return floods
    return TflRoadProvider(TflSource(session), located.reader, now=lambda: NOW)


@pytest.mark.usefixtures("production_logging")
@pytest.mark.parametrize("kind", PLACE_PROVIDERS)
def test_a_place_providers_cycle_logs_no_place_however_its_postcodes_are_stored(caplog, monkeypatch, nowhere, kind):
    """Through the runner, with a postcode stored each way, and nothing to be had for the one that's placed.

    One placed (whose upstream can't be reached), one not placed yet, one whose stored location can't be read (its
    error's text would quote what's there), one that doesn't exist, and a Norwegian one.
    """
    monkeypatch.setattr(met_office_main, "FEED_URL", f"{nowhere}/Region/{{region}}")
    monkeypatch.setattr(tfl_client, "TFL_URL", nowhere)
    located = FakeLocations(Postcode.model_validate(SOUTHWARK), not_found=("ZZ1 1ZZ",))
    # Not a StoredLocation (no fetched_at): one from another version, say. Its error's text would quote it.
    located.store.set(keys.location("YO1 7HH"), json.dumps({"postcode": "YO1 7HH", "found": True, "location": YORK}))
    provider = place_provider(kind, located, retrying_session())

    async def cycle():
        await located.redis.sadd(keys.subscription(keys.POSTCODE), "SE17 3LL", "E1 6AN", "YO1 7HH", "ZZ1 1ZZ", "3015")
        return await ProviderRunner(provider, located.redis).run_cycle()

    assert asyncio.run(cycle()) == 0

    failure, notices = PLACE_PROVIDERS[kind]
    for said in (
        (
            f"No location in the store yet for 1 of 4 postcodes, so no {notices} for them this cycle "
            "(provider-locations writes them)"
        ),
        (
            "Couldn't read the location in the store of 1 of 4 postcodes (not a StoredLocation), "
            f"so no {notices} for them this cycle"
        ),
        f"1 of 4 postcodes don't exist, so no {notices} for them",
        failure,
    ):
        assert said in caplog.messages
    assert not [record for record in caplog.records if record.name.startswith("urllib3")]
    assert_nothing_says_where(
        caplog,
        [
            *traces("SE17 3LL", "Southwark", "London", LATITUDE, LONGITUDE),
            *traces("E1 6AN"),
            *traces("YO1 7HH", "York", YORK["latitude"], YORK["longitude"]),
            *traces("ZZ1 1ZZ"),
            *traces("3015"),
        ],
    )


@pytest.mark.usefixtures("production_logging")
@pytest.mark.parametrize("kind", PLACE_PROVIDERS)
def test_a_place_provider_that_cant_read_the_store_logs_no_key(caplog, monkeypatch, kind):
    """A failed command's text can quote it, keys and all: each key is a postcode's place."""
    located = FakeLocations(Postcode.model_validate(SOUTHWARK))

    async def refused(names):
        message = f"Command MGET {' '.join(names)} not allowed when used memory > 'maxmemory'"
        raise RedisError(message)

    monkeypatch.setattr(located.redis, "mget", refused)
    provider = place_provider(kind, located, requests.Session())

    with responses.RequestsMock():  # and nothing asked of anyone else
        assert asyncio.run(provider.fetch(["SE17 3LL"])) == {}

    _, notices = PLACE_PROVIDERS[kind]
    assert caplog.messages == [
        (
            "Couldn't read from the store where the batch's postcodes are (1), "
            f"so no {notices} for them this cycle: RedisError"
        )
    ]
    assert_nothing_says_where(caplog, traces("SE17 3LL", "Southwark", "London", LATITUDE, LONGITUDE))


# --- TfL's app key ---------------------------------------------------------------------------------------------------

APP_KEY = "0123456789abcdef0123456789abcdef"


def tfl_provider(kind: str, session: requests.Session):
    """Return the TfL provider of `kind`, asking TfL with APP_KEY through `session`, and the ids it's subscribed to."""
    tfl = TflSource(session, APP_KEY)
    if kind == "departures":
        return TflProvider(tfl, now=lambda: NOW), ["490000123W"]
    if kind == "line status":
        return TflLineStatusProvider(tfl, now=lambda: NOW), ["490000123W"]
    if kind == "stations":
        return TflStationProvider(tfl, now=lambda: NOW), ["490000123W"]
    # Placed in the store already, as provider-locations writes it: only TfL is asked.
    roads = TflRoadProvider(tfl, FakeLocations(Postcode.model_validate(SOUTHWARK)).reader, now=lambda: NOW)
    return roads, ["SE17 3LL"]


@pytest.mark.usefixtures("production_logging")
@pytest.mark.parametrize("failure", ["refused", "unreachable"])
@pytest.mark.parametrize("kind", ["departures", "line status", "stations", "roads"])
def test_a_failed_tfl_request_logs_no_app_key(caplog, monkeypatch, nowhere, kind, failure):
    """A TfL error's text quotes its URL, app_key=... and all: refused (an HTTP error), or not reached at all."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as api:
        if failure == "refused":
            api.add(responses.GET, re.compile(rf"{re.escape(TFL_URL)}/.*"), status=403, json={"message": "Forbidden"})
        else:
            monkeypatch.setattr(tfl_client, "TFL_URL", nowhere)
            api.add_passthru(nowhere)
        provider, ids = tfl_provider(kind, retrying_session())
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        asyncio.run(redis.sadd(keys.subscription(provider.name), *ids))

        assert asyncio.run(ProviderRunner(provider, redis).run_cycle()) == 0

    assert [record for record in caplog.records if record.levelno >= logging.WARNING]  # the failure is logged
    assert APP_KEY not in caplog.text
    assert "app_key" not in caplog.text
