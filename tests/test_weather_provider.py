"""The weather provider against mocked Open-Meteo (responses), and its stored forecast's round trip through JSON.

A typed postcode's place comes from the store (fakeredis), as provider-locations writes it: test_weather_postcodes.py
has those, both providers together.
"""

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
import requests
import responses
from location_factory import FakeLocations
from responses import matchers
from weather_factory import forecast_json

from countdown_api import fixtures
from countdown_providers.runner import ProviderRunner
from countdown_providers.weather.main import WeatherProvider, place_id_of
from countdown_renderer.weather.weather_forecast import weather_pages
from countdown_shared.store import keys
from countdown_shared.store.models import StoredForecast
from countdown_shared.weather.open_meteo import FORECAST_URL, GEOCODING_URL, PLACE_URL

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
LONDON = "london-gb-2643743"
LONDON_PLACE = {"id": 2643743, "name": "London", "latitude": 51.50853, "longitude": -0.12574, "country_code": "GB"}
OSLO = "oslo-no-3143244"
OSLO_PLACE = {"id": 3143244, "name": "Oslo", "latitude": 59.91273, "longitude": 10.74609, "country_code": "NO"}


def make_provider(located: FakeLocations | None = None) -> WeatherProvider:
    """Return a provider asking Open-Meteo on a plain session, and reading typed postcodes' places from `located`."""
    return WeatherProvider(requests.Session(), (located or FakeLocations()).reader, now=lambda: NOW)


def add_place(place: dict) -> None:
    responses.add(responses.GET, PLACE_URL, json=place, match=[matchers.query_param_matcher({"id": str(place["id"])})])


def add_forecast() -> None:
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())


def forecast_points() -> list[tuple[float, float]]:
    """Return the (latitude, longitude) of every forecast asked for, in order."""
    points = []
    for call in responses.calls:
        if call.request.url.startswith(FORECAST_URL):
            query = parse_qs(urlparse(call.request.url).query)
            points.append((float(query["latitude"][0]), float(query["longitude"][0])))
    return points


def calls_to(url: str) -> int:
    return sum(call.request.url.startswith(url) for call in responses.calls)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (LONDON, 2643743),
        ("saint-john-ca-6138517", 6138517),
        ("gb-2643743", 2643743),  # a place whose name has no ASCII slug
        ("london", None),
        ("sw1a-1aa", None),
        ("", None),
        # Slugs of typed text that end in a number, with no country before it: names, not GeoNames ids.
        ("0150", None),
        ("oslo-0150", None),
        ("route-66", None),
        ("london-gb-2643743-x", None),
    ],
)
def test_place_id_of_takes_the_trailing_geonames_id(location, expected):
    assert place_id_of(location) == expected


# The keys countdown-server's weather_place.weather_key() makes: its tests read the same file.
WEATHER_KEYS = fixtures.load("weather_keys.json")["keys"]


@pytest.mark.parametrize("example", WEATHER_KEYS, ids=[example["key"] for example in WEATHER_KEYS])
def test_place_id_of_reads_the_keys_the_broker_makes(example):
    assert place_id_of(example["key"]) == example["place_id"]


@responses.activate
def test_fetch_looks_a_place_up_by_its_id_and_forecasts_there():
    add_place(LONDON_PLACE)
    add_forecast()
    results = asyncio.run(make_provider().fetch([LONDON]))
    assert list(results) == [LONDON]  # keyed by the location key, not the Redis key: the runner adds that
    stored = results[LONDON]
    assert stored.location == LONDON
    assert stored.fetched_at == NOW
    assert stored.forecast.utc_offset_seconds == forecast_json()["utc_offset_seconds"]
    assert forecast_points() == [(LONDON_PLACE["latitude"], LONDON_PLACE["longitude"])]
    assert calls_to(GEOCODING_URL) == 0


@responses.activate
def test_each_place_gets_its_own_forecast():
    add_place(LONDON_PLACE)
    add_place(OSLO_PLACE)
    add_forecast()
    results = asyncio.run(make_provider().fetch([LONDON, OSLO]))
    assert set(results) == {LONDON, OSLO}
    assert forecast_points() == [
        (LONDON_PLACE["latitude"], LONDON_PLACE["longitude"]),
        (OSLO_PLACE["latitude"], OSLO_PLACE["longitude"]),
    ]


@responses.activate
def test_a_place_is_looked_up_once_then_cached():
    add_place(LONDON_PLACE)
    add_forecast()
    provider = make_provider()
    asyncio.run(provider.fetch([LONDON]))
    asyncio.run(provider.fetch([LONDON]))
    assert calls_to(PLACE_URL) == 1
    assert calls_to(FORECAST_URL) == 2


@responses.activate
def test_a_key_without_an_id_is_geocoded_by_name():
    responses.add(
        responses.GET,
        GEOCODING_URL,
        json={"results": [{"name": "Tromsø", "latitude": 69.6489, "longitude": 18.95508}]},
        match=[matchers.query_param_matcher({"name": "tromso", "count": "1", "format": "json"})],
    )
    add_forecast()
    results = asyncio.run(make_provider().fetch(["tromso"]))
    assert list(results) == ["tromso"]
    assert forecast_points() == [(69.6489, 18.95508)]


@responses.activate
def test_a_slug_ending_in_a_number_is_geocoded_by_name_not_looked_up_by_id():
    responses.add(
        responses.GET,
        GEOCODING_URL,
        json={"results": [{"name": "Oslo", "latitude": 59.91, "longitude": 10.75}]},
        match=[matchers.query_param_matcher({"name": "oslo 0150", "count": "1", "format": "json"})],
    )
    add_forecast()
    assert list(asyncio.run(make_provider().fetch(["oslo-0150"]))) == ["oslo-0150"]
    assert calls_to(PLACE_URL) == 0
    assert forecast_points() == [(59.91, 10.75)]


@responses.activate
def test_a_place_open_meteo_cant_find_is_left_out_and_asked_again_next_time(caplog):
    responses.add(responses.GET, GEOCODING_URL, json={})  # Open-Meteo leaves "results" out when nothing matches
    provider = make_provider()
    assert asyncio.run(provider.fetch(["nowhere-at-all"])) == {}
    assert asyncio.run(provider.fetch(["nowhere-at-all"])) == {}
    assert calls_to(GEOCODING_URL) == 2
    assert calls_to(FORECAST_URL) == 0
    assert "Open-Meteo can't find a subscribed place, so it has no weather" in caplog.messages
    assert "nowhere" not in caplog.text  # a place says where a screen is


@responses.activate
def test_one_failing_place_doesnt_lose_the_rest_of_the_batch(caplog):
    responses.add(
        responses.GET,
        PLACE_URL,
        status=400,
        json={"error": True, "reason": "Not found"},
        match=[matchers.query_param_matcher({"id": "999"})],
    )
    add_place(OSLO_PLACE)
    add_forecast()
    results = asyncio.run(make_provider().fetch(["atlantis-xx-999", OSLO]))
    assert list(results) == [OSLO]
    assert "Couldn't fetch the weather for a subscribed place: HTTPError 400" in caplog.messages
    assert "atlantis" not in caplog.text  # a place says where a screen is


@responses.activate
def test_a_failing_forecast_is_left_out_but_keeps_the_cached_place():
    add_place(LONDON_PLACE)
    responses.add(responses.GET, FORECAST_URL, status=500)
    provider = make_provider()
    assert asyncio.run(provider.fetch([LONDON])) == {}
    assert LONDON in provider.coordinates


@responses.activate
def test_stored_forecast_round_trips_through_json_and_still_draws():
    """The runner stores model_dump_json() (our field names); a renderer must be able to read it back."""
    add_place(LONDON_PLACE)
    add_forecast()
    stored = asyncio.run(make_provider().fetch([LONDON]))[LONDON]
    read_back = StoredForecast.model_validate_json(stored.model_dump_json())
    assert read_back == stored
    assert weather_pages(read_back.forecast) == weather_pages(stored.forecast)


@responses.activate
def test_runner_writes_the_forecast_under_its_location_key_with_the_ttl():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    add_place(LONDON_PLACE)
    add_forecast()

    async def cycle():
        await redis.sadd(keys.subscription(keys.WEATHER), LONDON)
        provider = make_provider()
        written = await ProviderRunner(provider, redis).run_cycle()
        return written, await redis.get(keys.weather(LONDON)), await redis.ttl(keys.weather(LONDON)), provider.ttl_s

    written, value, ttl, ttl_s = asyncio.run(cycle())
    assert written == 1
    assert StoredForecast.model_validate_json(value).location == LONDON
    assert 0 < ttl <= ttl_s
