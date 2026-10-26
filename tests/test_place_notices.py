"""The providers of notices about a place read where each postcode is from the store, as provider-locations placed it.

provider-met-office, provider-floods and provider-tfl-notices' roads read `v1:location:<place>`
(locations.reader.LocationReader), so a postcode is looked up once in all, by provider-locations.

fixtures/place_notices.json is the parity check. `upstream` is what every API answered: postcodes.io and Kartverket
for each postcode, the Met Office's regional feeds, the EA's warnings in force and flood areas near each place, and
TfL's road disruptions. `before` was recorded from the providers running against those
answers when each asked postcodes.io for itself (provider-locations writing the locations the board reads): every
`v1:notices:` key they wrote, every request they made (bar postcodes.io), and what the notice board showed for each
postcode at `now`. From the same answers, the providers reading the store must write exactly the same keys, make the
same requests (and none to postcodes.io), and the board must show exactly the same notices. The postcodes cover each
kind of Met Office region (an English one twice, Wales, a Scottish council, Northern Ireland, one it has no region for
and so the whole UK's), places with flood warnings near them and without, and with road disruptions near them and
without, a postcode that doesn't exist and a Norwegian one, which these providers leave alone.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import fakeredis
import pytest
import requests
import responses
from location_factory import FakeLocations
from redis.exceptions import ConnectionError as RedisConnectionError
from responses import matchers
from test_utils import TEST_DEVICE_ID

from countdown_core.core import moment
from countdown_core.notices.location import POSTCODES_URL
from countdown_core.notices.norway import ADDRESSES_URL
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.store import keys, reader
from countdown_core.store.bank_holidays import StoredBankHolidays
from countdown_core.store.metalerts import StoredMetAlerts
from countdown_core.tfl.models import Postcode
from countdown_core.tfl.tfl_client import TFL_URL, TflSource
from countdown_providers import runner as runner_module
from countdown_providers.floods import main as floods_main
from countdown_providers.floods.main import FLOOD_AREAS_URL, FLOODS_URL, FloodsProvider
from countdown_providers.locations.main import LocationsProvider
from countdown_providers.locations.reader import LocationReader
from countdown_providers.met_office import main as met_office_main
from countdown_providers.met_office.main import FEED_URL, MetOfficeProvider
from countdown_providers.runner import ProviderRunner
from countdown_providers.tfl_notices import main as tfl_notices_main
from countdown_providers.tfl_notices.main import TflRoadProvider

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "place_notices.json").read_text(encoding="utf-8"))
UPSTREAM = FIXTURE["upstream"]
BEFORE = FIXTURE["before"]
POSTCODES = FIXTURE["postcodes"]
NOW = datetime.fromisoformat(FIXTURE["now"])
LOOKUPS = (POSTCODES_URL, ADDRESSES_URL)


def answer_lookups(api) -> None:
    """Answer postcodes.io and Kartverket for each postcode, as the fixture has them: provider-locations' lookups."""
    for postcode, answer in UPSTREAM["postcodes_io"].items():
        url = f"{POSTCODES_URL}/{postcode.replace(' ', '%20')}"
        api.add(responses.GET, url, status=answer["status"], json=answer["json"])
    for postcode, answer in UPSTREAM["kartverket"].items():
        match = [matchers.query_param_matcher({"postnummer": postcode}, strict_match=False)]
        api.add(responses.GET, ADDRESSES_URL, json=answer, match=match)


def answer_notices(api) -> None:
    """Answer the Met Office, the EA and TfL as the fixture has them; the EA's flood areas only near each place."""
    for region, feed in UPSTREAM["met_office"].items():
        api.add(responses.GET, FEED_URL.format(region=region), body=feed)
    api.add(responses.GET, FLOODS_URL, json=UPSTREAM["ea_in_force"])
    for postcode, areas in UPSTREAM["ea_flood_areas"].items():
        place = UPSTREAM["postcodes_io"][postcode]["json"]["result"]
        near = {"lat": str(place["latitude"]), "long": str(place["longitude"]), "dist": "5", "_limit": "2000"}
        api.add(responses.GET, FLOOD_AREAS_URL, json=areas, match=[matchers.query_param_matcher(near)])
    api.add(responses.GET, f"{TFL_URL}/Road/all/Disruption", json=UPSTREAM["tfl_roads"])


def notice_json(notice) -> dict:
    return {
        "source": notice.source,
        "severity": notice.severity.name,
        "text": notice.text,
        "valid_until": notice.valid_until and notice.valid_until.isoformat(),
        "display_time": notice.display_time and notice.display_time.isoformat(),
    }


@pytest.fixture
def stores(monkeypatch):
    """Return the providers' (async) side of one store and the renderer's (sync) side, which reader.store() gives."""
    server = fakeredis.FakeServer()
    renderer = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: renderer)
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=True), renderer


def place_providers(redis) -> list:
    """Make the three, each reading where the postcodes are on `redis`, the client its runner uses, as main() does."""
    session = requests.Session()
    locations = LocationReader(redis)
    return [
        MetOfficeProvider(session, locations, now=lambda: NOW),
        FloodsProvider(session, locations, now=lambda: NOW),
        TflRoadProvider(TflSource(session), locations, now=lambda: NOW),
    ]


def run_cycle(redis, *providers) -> None:
    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), *POSTCODES)
        for provider in providers:
            await ProviderRunner(provider, redis).run_cycle()

    asyncio.run(cycle())


def board_shows(renderer, monkeypatch) -> dict:
    """Return what the notice board shows for each postcode, at NOW, with the other notices as when it was recorded."""
    gov_uk = json.loads((FIXTURES / "bank_holidays.json").read_text(encoding="utf-8"))
    renderer.set(keys.bank_holidays(), StoredBankHolidays(fetched_at=NOW, divisions=gov_uk).model_dump_json())
    metalerts = json.loads((FIXTURES / "metalerts_current.json").read_text(encoding="utf-8"))
    renderer.set(keys.metalerts(), StoredMetAlerts.model_validate(metalerts).model_dump_json())

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    # The moment the board draws at (core.moment), as a render sets it.
    monkeypatch.setattr(moment, "now", lambda: Frozen.now(UTC))
    shown = {}
    for postcode in POSTCODES:
        country = "NO" if postcode.isdigit() else "GB"
        board = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(postcode=postcode, country=country))
        with responses.RequestsMock() as nothing:  # the board asks nobody: any request would fail
            panel = asyncio.run(board.update())
            assert len(nothing.calls) == 0
        shown[postcode] = {
            "message": getattr(panel, "message", None),
            "notices": [notice_json(n) for n in panel.notices] if hasattr(panel, "notices") else None,
        }
    return shown


# --- Parity ----------------------------------------------------------------------------------------------------------


def test_from_the_same_answers_the_providers_store_and_the_board_shows_what_they_did_when_each_looked_up(
    stores, monkeypatch
):
    redis, renderer = stores
    with responses.RequestsMock(assert_all_requests_are_fired=False) as api:
        answer_lookups(api)
        run_cycle(redis, LocationsProvider(requests.Session(), now=lambda: NOW))
        looked_up = [call.request.url for call in api.calls]
    with responses.RequestsMock(assert_all_requests_are_fired=False) as api:
        answer_notices(api)  # and no one to say where a postcode is: a request to postcodes.io would fail
        run_cycle(redis, *place_providers(redis))
        asked = sorted(call.request.url for call in api.calls)

    stored = {key: json.loads(renderer.get(key)) for key in sorted(renderer.keys("v1:notices:*"))}
    assert stored == BEFORE["stored"]
    assert asked == BEFORE["requests"]  # the same requests to the Met Office, the EA and TfL, and nothing else
    assert board_shows(renderer, monkeypatch) == BEFORE["board"]
    # Each postcode looked up once in all, by provider-locations; the three asking for themselves made 24 lookups.
    assert sorted(url.split("?")[0] for url in looked_up if url.startswith(POSTCODES_URL)) == sorted(
        f"{POSTCODES_URL}/{postcode.replace(' ', '%20')}" for postcode in UPSTREAM["postcodes_io"]
    )
    assert BEFORE["postcodes_io_requests"] == 3 * len(UPSTREAM["postcodes_io"])


def test_the_recorded_cycle_covers_every_kind_of_place():
    stored = BEFORE["stored"]
    regions = {value["region"] for key, value in stored.items() if key.startswith("v1:notices:met-office:")}
    floods = [value["floods"]["items"] for key, value in stored.items() if key.startswith("v1:notices:floods:")]
    roads = [value["disruptions"] for key, value in stored.items() if key.startswith("v1:notices:tfl-roads:")]

    assert regions == {"se", "yh", "wl", "dg", "ni", "UK"}
    assert any(floods)
    assert not all(floods)
    assert any(roads)
    assert not all(roads)
    assert BEFORE["board"]["ZZ1 1ZZ"]["message"] == "Unknown postcode"
    assert BEFORE["board"]["0150"]["notices"]
    assert not any(key.endswith(("ZZ11ZZ", ":0150")) for key in stored)


# --- The reader ------------------------------------------------------------------------------------------------------

SOUTHWARK = Postcode(
    postcode="SE17 3LL", latitude=51.49, longitude=-0.1, country="England", region="London", admin_district="Southwark"
)
YORK = Postcode(
    postcode="YO1 7HH", latitude=53.96, longitude=-1.08, country="England", region="Yorkshire and The Humber"
)


class CountingMget:
    """Wraps a fake Redis's MGET, counting the calls."""

    def __init__(self, redis) -> None:
        self.mget = redis.mget
        self.calls: list[list[str]] = []

    async def __call__(self, names):
        self.calls.append(list(names))
        return await self.mget(names)


def places(located: FakeLocations, postcodes: list[str]) -> dict[str, Postcode]:
    return asyncio.run(located.reader.places(postcodes, notices="tests"))


def test_the_reader_reads_a_batch_in_one_mget_and_keeps_its_order(monkeypatch):
    located = FakeLocations(SOUTHWARK, YORK)
    mget = CountingMget(located.redis)
    monkeypatch.setattr(located.redis, "mget", mget)

    found = places(located, ["YO1 7HH", "SE17 3LL"])

    assert list(found) == ["YO1 7HH", "SE17 3LL"]
    assert found["SE17 3LL"] == SOUTHWARK
    assert mget.calls == [["v1:location:YO17HH", "v1:location:SE173LL"]]


def test_the_reader_asks_nothing_for_no_postcodes(monkeypatch):
    located = FakeLocations()
    monkeypatch.setattr(located.redis, "mget", lambda names: pytest.fail("MGET needs a key"))

    assert places(located, []) == {}


def test_the_reader_leaves_out_what_it_cant_place_and_says_how_many(caplog):
    located = FakeLocations(SOUTHWARK, not_found=("ZZ1 1ZZ",))
    located.store.set(keys.location("YO1 7HH"), "not JSON")

    with caplog.at_level(logging.DEBUG, logger="countdown_providers.locations.reader"):
        found = places(located, ["SE17 3LL", "E1 6AN", "YO1 7HH", "ZZ1 1ZZ"])

    assert list(found) == ["SE17 3LL"]
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "INFO",
            (
                "No location in the store yet for 1 of 4 postcodes, so no tests for them this cycle "
                "(provider-locations writes them)"
            ),
        ),
        (
            "WARNING",
            (
                "Couldn't read the location in the store of 1 of 4 postcodes (not a StoredLocation), "
                "so no tests for them this cycle"
            ),
        ),
        ("DEBUG", "1 of 4 postcodes don't exist, so no tests for them"),
    ]


def test_the_reader_says_nothing_when_every_postcode_is_placed(caplog):
    located = FakeLocations(SOUTHWARK)

    with caplog.at_level(logging.DEBUG, logger="countdown_providers.locations.reader"):
        assert list(places(located, ["SE17 3LL"])) == ["SE17 3LL"]

    assert caplog.records == []


def test_the_reader_places_nothing_while_redis_cant_be_read_and_doesnt_raise(monkeypatch, caplog):
    located = FakeLocations(SOUTHWARK)

    async def unreachable(names):
        raise RedisConnectionError

    monkeypatch.setattr(located.redis, "mget", unreachable)

    assert places(located, ["SE17 3LL"]) == {}
    assert caplog.messages == [
        (
            "Couldn't read from the store where the batch's postcodes are (1), "
            "so no tests for them this cycle: ConnectionError"
        )
    ]


@pytest.mark.parametrize(
    "make", [lambda r: place_providers(r)[0], lambda r: place_providers(r)[1], lambda r: place_providers(r)[2]]
)
def test_a_postcode_the_store_cant_place_keeps_its_notices_and_the_others_are_written(make, monkeypatch):
    """Its notices' key isn't written over (it lapses after the TTL if this lasts), and the batch's others carry on."""
    located = FakeLocations(SOUTHWARK, YORK)
    provider = make(located.redis)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as api:
        answer_notices(api)
        api.add(
            responses.GET, FLOOD_AREAS_URL, json={"items": []}
        )  # near SOUTHWARK and YORK, which aren't the fixture's

        async def cycle():
            await located.redis.sadd(keys.subscription(keys.POSTCODE), "SE17 3LL", "YO1 7HH")
            runner = ProviderRunner(provider, located.redis)
            await runner.run_cycle()
            first = await located.redis.get(provider.output_key("YO1 7HH"))
            located.remove("YO1 7HH")  # its location lapses, or can't be read
            return first, await runner.run_cycle()

        first, written = asyncio.run(cycle())

    assert written == 1  # SE17 3LL's
    assert asyncio.run(located.redis.get(provider.output_key("YO1 7HH"))) == first


# --- The entry points ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "provider_type"), [(met_office_main, MetOfficeProvider), (floods_main, FloodsProvider)]
)
def test_main_reads_the_locations_on_the_runners_own_client(module, provider_type, monkeypatch):
    """Through main(), as the container starts it: one Redis client, from the environment, for both."""
    ran = []

    class FakeRunner:
        def __init__(self, provider, redis, *, shard):
            ran.append((provider, redis))

        async def run(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(runner_module, "ProviderRunner", FakeRunner)
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    monkeypatch.setenv("REDIS_URL", "redis://redis.example:6379/0")

    module.main()

    [(provider, redis)] = ran
    assert isinstance(provider, provider_type)
    assert provider.locations.redis is redis
    assert redis.connection_pool.connection_kwargs["host"] == "redis.example"


# --- A new postcode: its notices as soon as it's placed --------------------------------------------------------------

# A test that waits this long has failed: the providers' next tick is 5 minutes away (see `just_after_a_tick`), so
# only provider-locations' announcement can run their second cycle.
WAIT_S = 5


def just_after_a_tick() -> float:
    """Return the runners' time: always just after a tick, so the next is a whole interval (5 minutes) away."""
    return 0.0


def count_writes(provider) -> list[int]:
    """Wrap `provider`'s fetch to record, each time it's called, how many values it returned for the runner to write."""
    written: list[int] = []
    fetch = provider.fetch

    async def counted(ids):
        results = await fetch(ids)
        written.append(len(results))
        return results

    provider.fetch = counted
    return written


def hold_lookups(provider) -> asyncio.Event:
    """Hold `provider`'s (provider-locations') lookups until the event returned is set: still under way."""
    done = asyncio.Event()
    fetch = provider.fetch

    async def slow(ids):
        await done.wait()
        return await fetch(ids)

    provider.fetch = slow
    return done


async def until(condition) -> None:
    """Wait (polling: fakeredis has no event for any of these) until `condition()`, an awaitable, is true."""
    async with asyncio.timeout(WAIT_S):
        while not await condition():  # noqa: ASYNC110 -- nothing to wait on
            await asyncio.sleep(0.01)


def test_a_new_postcodes_notices_are_written_as_soon_as_provider_locations_has_placed_it():
    """Through the real runners, each provider on its own client as in its own process, as a config save starts them.

    The broker's nudge runs every provider at once, so the place providers read before provider-locations' lookups
    are done, find no location and write nothing; then provider-locations writes the places and announces them on
    `v1:location:changed`, and each of them runs again at once, rather than 5 minutes later at its next tick, and
    writes exactly what the recorded cycle did.
    """
    server = fakeredis.FakeServer()
    store = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

    def client():
        return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

    # Met Office, floods and TfL roads, each reading the places on its runner's own client, as its main() does.
    dependents = []
    for index in range(3):
        redis = client()
        dependents.append((place_providers(redis)[index], redis))
    writes = [count_writes(provider) for provider, _ in dependents]
    locations = LocationsProvider(requests.Session(), now=lambda: NOW)
    looked_up = hold_lookups(locations)

    async def notices():
        return sorted(await store.keys("v1:notices:*"))

    async def scenario():
        await store.sadd(keys.subscription(keys.POSTCODE), *POSTCODES)
        runners = [ProviderRunner(locations, client(), clock=just_after_a_tick)] + [
            ProviderRunner(provider, redis, clock=just_after_a_tick) for provider, redis in dependents
        ]
        tasks = [asyncio.create_task(runner.run()) for runner in runners]
        try:
            # Each has read, found no place and written nothing, and is listening.
            async def all_listening():
                return all(writes) and (await store.pubsub_numsub(keys.LOCATION_CHANGED))[0][1] == len(dependents)

            await until(all_listening)
            assert writes == [[0]] * len(dependents)
            assert await notices() == []
            looked_up.set()

            async def all_written():
                return await notices() == sorted(BEFORE["stored"])

            await until(all_written)
        finally:
            for runner in runners:
                runner.stop()
            await asyncio.wait_for(asyncio.gather(*tasks), WAIT_S)
        return {key: json.loads(await store.get(key)) for key in await notices()}

    with responses.RequestsMock(assert_all_requests_are_fired=False) as api:
        answer_lookups(api)
        answer_notices(api)
        stored = asyncio.run(scenario())

    assert stored == BEFORE["stored"]
    assert all(written[0] == 0 and sum(written) > 0 for written in writes)


def test_only_the_providers_that_read_places_wake_on_them_and_only_provider_locations_announces_them():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    lines, stations, roads = tfl_notices_main.providers(LocationReader(redis))
    met_office, floods, _ = place_providers(redis)

    for provider in (met_office, floods, roads):
        assert ProviderRunner(provider, redis).wakes_on == (keys.SUB_CHANGED, keys.LOCATION_CHANGED)
    for provider in (lines, stations):
        assert ProviderRunner(provider, redis).wakes_on == (keys.SUB_CHANGED,)
    assert LocationsProvider(requests.Session()).announces == keys.LOCATION_CHANGED
    assert not any(hasattr(provider, "announces") for provider in (met_office, floods, lines, stations, roads))
