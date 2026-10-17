"""The weather provider against mocked Open-Meteo (responses), and its stored forecast's round trip through JSON."""

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
import requests
import responses
from responses import matchers
from weather_factory import forecast_json

from countdown_core.store import keys
from countdown_core.store.models import StoredForecast
from countdown_core.weather.open_meteo import FORECAST_URL, GEOCODING_URL, PLACE_URL
from countdown_core.weather.weather_forecast import weather_pages
from countdown_providers.runner import ProviderRunner
from countdown_providers.weather.main import WeatherProvider, place_id_of

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
LONDON = "london-gb-2643743"
LONDON_PLACE = {"id": 2643743, "name": "London", "latitude": 51.50853, "longitude": -0.12574, "country_code": "GB"}
OSLO = "oslo-no-3143244"
OSLO_PLACE = {"id": 3143244, "name": "Oslo", "latitude": 59.91273, "longitude": 10.74609, "country_code": "NO"}


def make_provider() -> WeatherProvider:
    return WeatherProvider(requests.Session(), now=lambda: NOW)


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
    [(LONDON, 2643743), ("saint-john-ca-6138517", 6138517), ("london", None), ("sw1a-1aa", None), ("", None)],
)
def test_place_id_of_takes_the_trailing_geonames_id(location, expected):
    assert place_id_of(location) == expected


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
def test_a_place_open_meteo_cant_find_is_left_out_and_asked_again_next_time(caplog):
    responses.add(responses.GET, GEOCODING_URL, json={})  # Open-Meteo leaves "results" out when nothing matches
    provider = make_provider()
    assert asyncio.run(provider.fetch(["nowhere-at-all"])) == {}
    assert asyncio.run(provider.fetch(["nowhere-at-all"])) == {}
    assert calls_to(GEOCODING_URL) == 2
    assert calls_to(FORECAST_URL) == 0
    assert "can't find a place for 'nowhere-at-all'" in caplog.text


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
    assert "Couldn't fetch the weather for atlantis-xx-999" in caplog.text


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
