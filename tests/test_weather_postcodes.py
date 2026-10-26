"""A typed postcode's weather: provider-locations places it (`v1:location:<place>`), provider-weather reads it there.

A postcode (or a postcode district) typed as a screen's weather location, not picked as a place, is subscribed as its
slug (`sw1a-1aa`, `se17`). provider-locations, the one process that looks postcodes up, writes where they are, and
the weather provider reads it from the store, so it makes no request but Open-Meteo's forecast. postcodes.io and
Open-Meteo are mocked (responses), Redis is fakeredis.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
import requests
import responses
from location_factory import FakeLocations
from redis.exceptions import RedisError
from weather_factory import forecast_json

from countdown_api import fixtures
from countdown_providers.locations.main import LocationsProvider, WeatherPostcodesProvider
from countdown_providers.runner import ProviderRunner
from countdown_providers.weather.main import WeatherProvider
from countdown_shared.notices.location import OUTCODES_URL, POSTCODES_URL, lookup_outcode
from countdown_shared.store import keys
from countdown_shared.store.location import StoredLocation, weather_postcode
from countdown_shared.store.models import StoredForecast
from countdown_shared.tfl.models import Postcode
from countdown_shared.weather.open_meteo import FORECAST_URL, GEOCODING_URL, PLACE_URL

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
# Coordinates odd enough that finding them in a log can't be chance.
WESTMINSTER = Postcode(
    postcode="SW1A 1AA",
    latitude=51.50101,
    longitude=-0.14159,
    country="England",
    region="London",
    admin_district="Westminster",
)
SE17_CENTRE = (51.48781, -0.09479)
SE17 = Postcode(postcode="SE17", latitude=SE17_CENTRE[0], longitude=SE17_CENTRE[1], country="England")
POSTCODES_IO_SW1A = {
    "status": 200,
    "result": {
        "postcode": "SW1A 1AA",
        "latitude": WESTMINSTER.latitude,
        "longitude": WESTMINSTER.longitude,
        "country": "England",
        "region": "London",
        "admin_district": "Westminster",
    },
}
POSTCODES_IO_SE17 = {
    "status": 200,
    "result": {
        "outcode": "SE17",
        "latitude": SE17_CENTRE[0],
        "longitude": SE17_CENTRE[1],
        "admin_district": ["Southwark", "Lambeth"],
        "country": ["England"],
    },
}
LONDON = "london-gb-2643743"
LONDON_PLACE = {"id": 2643743, "name": "London", "latitude": 51.50853, "longitude": -0.12574, "country_code": "GB"}


def forecast_points(calls=None) -> list[tuple[float, float]]:
    """Return the (latitude, longitude) of every forecast asked for (of `calls`, else responses'), in order."""
    points = []
    for call in responses.calls if calls is None else calls:
        if call.request.url.startswith(FORECAST_URL):
            query = parse_qs(urlparse(call.request.url).query)
            points.append((float(query["latitude"][0]), float(query["longitude"][0])))
    return points


def calls_to(url: str) -> int:
    return sum(call.request.url.startswith(url) for call in responses.calls)


def weather(located: FakeLocations) -> WeatherProvider:
    return WeatherProvider(requests.Session(), located.reader, now=lambda: NOW)


def assert_nothing_says_where(text: str, *traces: str | float) -> None:
    for trace in traces:
        assert str(trace).upper() not in text.upper(), f"the logs mention {trace!r}"


# --- Which weather keys are postcodes --------------------------------------------------------------------------------

# The keys countdown-server's weather_place.weather_key() makes: its tests read the same file.
WEATHER_KEYS = fixtures.load("weather_keys.json")["keys"]


@pytest.mark.parametrize(
    ("key", "postcode"),
    [
        ("sw1a-1aa", "SW1A 1AA"),
        ("sw1a1aa", "SW1A 1AA"),  # typed without its space
        ("e1-6an", "E1 6AN"),
        ("se17", "SE17"),  # a district
        ("n1", "N1"),
        # Not postcodes: Open-Meteo's to find.
        ("london", None),
        ("london-sw1a-1aa", None),
        ("sw1a-1", None),
        ("oslo-0150", None),
        ("0150", None),  # a Norwegian postcode is Open-Meteo's search, never Kartverket's
        (LONDON, None),
        ("gb-2643743", None),
        ("", None),
    ],
)
def test_weather_postcode_finds_the_postcode_a_key_is_the_slug_of_in_the_brokers_form(key, postcode):
    assert weather_postcode(key) == postcode


@pytest.mark.parametrize("example", WEATHER_KEYS, ids=[example["key"] for example in WEATHER_KEYS])
def test_of_the_keys_the_broker_makes_only_a_typed_postcodes_is_a_postcode(example):
    typed = example["config"]["weather_location"].strip()
    expected = "SW1A 1AA" if example["place_id"] is None and typed == "SW1A 1AA" else None
    assert weather_postcode(example["key"]) == expected


# --- provider-weather, reading the store -----------------------------------------------------------------------------


@responses.activate
def test_a_typed_postcode_is_forecast_where_the_store_places_it_without_asking_postcodes_io():
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())
    located = FakeLocations(WESTMINSTER, SE17)

    results = asyncio.run(weather(located).fetch(["sw1a-1aa", "se17"]))

    assert list(results) == ["sw1a-1aa", "se17"]  # by the weather key, which the renderer reads under
    assert results["sw1a-1aa"].location == "sw1a-1aa"
    assert forecast_points() == [(WESTMINSTER.latitude, WESTMINSTER.longitude), SE17_CENTRE]
    assert len(responses.calls) == 2  # the forecasts only: not postcodes.io, nor Open-Meteo's geocoder


@responses.activate
def test_other_keys_are_still_looked_up_with_open_meteo_beside_a_typed_postcode():
    responses.add(responses.GET, PLACE_URL, json=LONDON_PLACE)
    responses.add(
        responses.GET, GEOCODING_URL, json={"results": [{"name": "Oslo", "latitude": 59.91, "longitude": 10.75}]}
    )
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())

    results = asyncio.run(weather(FakeLocations(WESTMINSTER)).fetch([LONDON, "oslo-0150", "sw1a-1aa"]))

    assert list(results) == [LONDON, "oslo-0150", "sw1a-1aa"]
    assert calls_to(PLACE_URL) == 1
    assert calls_to(GEOCODING_URL) == 1
    (search,) = [call.request.url for call in responses.calls if call.request.url.startswith(GEOCODING_URL)]
    assert parse_qs(urlparse(search).query)["name"] == ["oslo 0150"]
    assert calls_to(POSTCODES_URL) == calls_to(OUTCODES_URL) == 0


@responses.activate
def test_a_postcode_the_store_cant_place_is_left_out_this_cycle_and_logged_without_naming_it(caplog):
    """Not placed yet, not a StoredLocation, or not a postcode that exists: as for the providers of place notices."""
    caplog.set_level(logging.DEBUG)
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())
    located = FakeLocations(WESTMINSTER, not_found=("ZZ1 1ZZ",))
    located.store.set(keys.location("E1 6AN"), json.dumps({"postcode": "E1 6AN", "found": True}))
    provider = weather(located)

    results = asyncio.run(provider.fetch(["e1-6an", "n1", "sw1a-1aa", "zz1-1zz"]))

    assert list(results) == ["sw1a-1aa"]
    assert forecast_points() == [(WESTMINSTER.latitude, WESTMINSTER.longitude)]
    assert len(responses.calls) == 1  # nothing asked about the others, and nothing cached for them
    assert provider.coordinates == {}
    assert (
        "No location in the store yet for 1 of 4 postcodes, so no weather for them this cycle "
        "(provider-locations writes them)"
    ) in caplog.messages
    assert "1 of 4 postcodes don't exist, so no weather for them" in caplog.messages
    assert_nothing_says_where(
        caplog.text, "SW1A", "E1 6AN", "E16AN", "ZZ1", "Westminster", WESTMINSTER.latitude, WESTMINSTER.longitude
    )


@responses.activate
def test_a_postcode_is_read_from_the_store_every_cycle_so_a_move_is_used_at_once():
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())
    located = FakeLocations(WESTMINSTER)
    provider = weather(located)
    asyncio.run(provider.fetch(["sw1a-1aa"]))

    located.put(WESTMINSTER.model_copy(update={"latitude": 51.5, "longitude": -0.14}))
    asyncio.run(provider.fetch(["sw1a-1aa"]))
    located.remove("SW1A 1AA")
    assert asyncio.run(provider.fetch(["sw1a-1aa"])) == {}

    assert forecast_points() == [(WESTMINSTER.latitude, WESTMINSTER.longitude), (51.5, -0.14)]


@responses.activate
def test_a_store_that_cant_be_read_loses_only_the_typed_postcodes(monkeypatch, caplog):
    responses.add(responses.GET, PLACE_URL, json=LONDON_PLACE)
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())
    located = FakeLocations(WESTMINSTER)

    async def refused(names):
        message = f"Command MGET {' '.join(names)} not allowed"
        raise RedisError(message)

    monkeypatch.setattr(located.redis, "mget", refused)

    assert list(asyncio.run(weather(located).fetch([LONDON, "sw1a-1aa"]))) == [LONDON]
    assert "Couldn't read from the store where the batch's postcodes are (1), so no weather for them this cycle: " in (
        caplog.text
    )
    assert_nothing_says_where(caplog.text, "SW1A")


# --- provider-locations, placing the weather's postcodes -------------------------------------------------------------


@pytest.fixture
def postcodes_io():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{POSTCODES_URL}/SW1A%201AA", json=POSTCODES_IO_SW1A)
        rsps.add(responses.GET, f"{OUTCODES_URL}/SE17", json=POSTCODES_IO_SE17)
        rsps.add(responses.GET, f"{POSTCODES_URL}/ZZ1%201ZZ", status=404, json={"status": 404})
        rsps.add(responses.GET, PLACE_URL, json=LONDON_PLACE)
        rsps.add(responses.GET, FORECAST_URL, json=forecast_json())
        yield rsps


def test_lookup_outcode_gives_the_districts_centre_as_a_postcode(postcodes_io):
    assert lookup_outcode(requests.Session(), "SE17") == Postcode(
        postcode="SE17", latitude=SE17_CENTRE[0], longitude=SE17_CENTRE[1], country="England"
    )


def test_the_weathers_postcodes_are_placed_by_their_own_postcode_and_other_keys_left_alone(postcodes_io):
    provider = WeatherPostcodesProvider(LocationsProvider(requests.Session(), now=lambda: NOW))

    results = asyncio.run(provider.fetch([LONDON, "se17", "sw1a-1aa", "tromso", "zz1-1zz"]))

    assert set(results) == {"se17", "sw1a-1aa", "zz1-1zz"}
    assert results["sw1a-1aa"] == StoredLocation(fetched_at=NOW, postcode="SW1A 1AA", found=True, location=WESTMINSTER)
    assert results["se17"] == StoredLocation(fetched_at=NOW, postcode="SE17", found=True, location=SE17)
    assert results["zz1-1zz"] == StoredLocation(fetched_at=NOW, postcode="ZZ1 1ZZ", found=False)
    assert [provider.output_key(key) for key in ("se17", "sw1a-1aa")] == ["v1:location:SE17", "v1:location:SW1A1AA"]
    assert not [call for call in postcodes_io.calls if call.request.url.startswith((PLACE_URL, GEOCODING_URL))]


def test_a_postcode_both_a_board_and_a_weather_key_have_is_looked_up_once(postcodes_io):
    postcodes = LocationsProvider(requests.Session(), now=lambda: NOW)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)

    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), "SW1A 1AA")
        await redis.sadd(keys.subscription(keys.WEATHER), "sw1a-1aa", "sw1a1aa")
        board = ProviderRunner(postcodes, redis)
        typed = ProviderRunner(WeatherPostcodesProvider(postcodes), redis)
        return await asyncio.gather(board.run_cycle(), typed.run_cycle()), await redis.keys("v1:location:*")

    assert asyncio.run(cycle()) == ([1, 2], ["v1:location:SW1A1AA"])
    assert sum(call.request.url.startswith(POSTCODES_URL) for call in postcodes_io.calls) == 1


def test_the_weather_provider_forecasts_where_provider_locations_placed_a_typed_postcode(postcodes_io, caplog):
    """Both providers' runners on one store, as on the host: postcodes.io asked once, by provider-locations alone."""
    caplog.set_level(logging.DEBUG)
    located = FakeLocations()
    lookups = LocationsProvider(requests.Session(), now=lambda: NOW)
    forecasts = WeatherProvider(requests.Session(), located.reader, now=lambda: NOW)

    async def cycles():
        await located.redis.sadd(keys.subscription(keys.WEATHER), LONDON, "se17", "sw1a-1aa", "zz1-1zz")
        weather_runner = ProviderRunner(forecasts, located.redis)
        before = await weather_runner.run_cycle()  # provider-locations hasn't placed them yet
        placed = await ProviderRunner(WeatherPostcodesProvider(lookups), located.redis).run_cycle()
        after = await weather_runner.run_cycle()
        values = {key: await located.redis.get(keys.weather(key)) for key in (LONDON, "se17", "sw1a-1aa", "zz1-1zz")}
        return before, placed, after, values

    before, placed, after, values = asyncio.run(cycles())

    assert (before, placed, after) == (1, 3, 3)
    assert values["zz1-1zz"] is None  # not a postcode that exists: no weather
    for key in (LONDON, "se17", "sw1a-1aa"):
        assert StoredForecast.model_validate_json(values[key]).location == key
    assert forecast_points(postcodes_io.calls) == [
        (LONDON_PLACE["latitude"], LONDON_PLACE["longitude"]),
        (LONDON_PLACE["latitude"], LONDON_PLACE["longitude"]),
        SE17_CENTRE,
        (WESTMINSTER.latitude, WESTMINSTER.longitude),
    ]
    postcode_lookups = [
        call
        for call in postcodes_io.calls
        if (parsed := urlparse(call.request.url)).scheme == "https" and parsed.hostname == "api.postcodes.io"
    ]
    assert len(postcode_lookups) == 3  # SE17, SW1A 1AA and ZZ1 1ZZ, once each, all by provider-locations
    assert_nothing_says_where(
        caplog.text, "SW1A", "SE17", "ZZ1", "Westminster", "Southwark", WESTMINSTER.latitude, SE17_CENTRE[0]
    )


# --- A newly placed typed postcode wakes the weather provider ---------------------------------------------------------

# A test that waits this long has failed: the weather's next tick is 10 minutes away (just_after_a_tick), so only
# provider-locations' announcement on `v1:location:changed` can run its second cycle.
WAIT_S = 5


def just_after_a_tick() -> float:
    """Return the runners' time: always just after a tick, so the next is a whole interval away."""
    return 0.0


async def until(condition) -> None:
    """Wait (polling: fakeredis has no event for any of these) until `condition()`, an awaitable, is true."""
    async with asyncio.timeout(WAIT_S):
        while not await condition():  # noqa: ASYNC110 -- nothing to wait on
            await asyncio.sleep(0.01)


def test_a_typed_postcodes_weather_is_written_as_soon_as_provider_locations_has_placed_it(postcodes_io):
    """Through the real runners' loops, each on its own client as in its own process.

    The weather provider runs first and finds no place for the typed postcodes (only London's forecast); then
    provider-locations' weather runner places them and announces it, and the weather provider runs again at once,
    rather than 10 minutes later at its next tick.
    """
    server = fakeredis.FakeServer()
    store = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

    def client():
        return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

    weather_redis = client()
    forecasts = WeatherProvider(requests.Session(), FakeLocations(server=server).reader, now=lambda: NOW)
    typed = WeatherPostcodesProvider(LocationsProvider(requests.Session(), now=lambda: NOW))
    wanted = [keys.weather(key) for key in (LONDON, "se17", "sw1a-1aa")]

    async def scenario():
        await store.sadd(keys.subscription(keys.WEATHER), LONDON, "se17", "sw1a-1aa")
        weather_runner = ProviderRunner(forecasts, weather_redis, clock=just_after_a_tick)
        runners = [weather_runner]
        tasks = [asyncio.create_task(weather_runner.run())]
        try:

            async def only_london_and_listening():
                return (await store.exists(keys.weather(LONDON))) == 1 and (
                    await store.pubsub_numsub(keys.LOCATION_CHANGED)
                )[0][1] == 1

            await until(only_london_and_listening)
            assert sorted(await store.keys("v1:weather:*")) == [keys.weather(LONDON)]
            locations_runner = ProviderRunner(typed, client(), clock=just_after_a_tick)
            runners.append(locations_runner)
            tasks.append(asyncio.create_task(locations_runner.run()))

            async def all_written():
                return sorted(await store.keys("v1:weather:*")) == sorted(wanted)

            await until(all_written)
        finally:
            for runner in runners:
                runner.stop()
            await asyncio.wait_for(asyncio.gather(*tasks), WAIT_S)

    asyncio.run(scenario())
    assert SE17_CENTRE in forecast_points(postcodes_io.calls)


def test_the_weather_provider_wakes_on_new_places_and_the_weathers_postcodes_are_announced_once_with_the_boards():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    postcodes = LocationsProvider(requests.Session(), now=lambda: NOW)
    typed = WeatherPostcodesProvider(postcodes)
    westminster = StoredLocation(fetched_at=NOW, postcode="SW1A 1AA", found=True, location=WESTMINSTER)

    assert ProviderRunner(weather(FakeLocations()), redis).wakes_on == (keys.SUB_CHANGED, keys.LOCATION_CHANGED)
    assert typed.announces == keys.LOCATION_CHANGED
    assert typed.is_news("sw1a-1aa", westminster) is True
    # The same place: under another slug, or by the board's runner, it's been announced already.
    assert typed.is_news("sw1a1aa", westminster) is False
    assert postcodes.is_news("SW1A 1AA", westminster) is False
    assert typed.is_news("zz1-1zz", StoredLocation(fetched_at=NOW, postcode="ZZ1 1ZZ", found=False)) is False
