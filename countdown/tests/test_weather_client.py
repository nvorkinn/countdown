import asyncio
import datetime as dt

import pytest
import requests
import responses
from test_utils import REGISTRATION

from countdown_core.config_server.models import WeatherConfig
from countdown_core.notices.location import is_postcode
from countdown_core.weather import weather_client
from countdown_core.weather.models import Weather
from countdown_core.weather.weather_client import FORECAST_URL, GEOCODING_URL, WeatherClient

GEOCODING_JSON = {"results": [{"name": "London", "latitude": 51.5, "longitude": -0.12, "country": "United Kingdom"}]}
FORECAST_JSON = {
    "current": {"time": "2026-01-15T12:00", "temperature_2m": 14.2, "weather_code": 2, "is_day": 1},
    "daily": {
        "time": ["2026-01-15"],
        "temperature_2m_max": [18.4],
        "temperature_2m_min": [9.1],
        "precipitation_probability_max": [40],
    },
}


def make_client(location: str = "London") -> WeatherClient:
    return WeatherClient(REGISTRATION, WeatherConfig(api_key="", location=location))


@responses.activate
def test_update_geocodes_then_fetches_forecast():
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)

    panel = asyncio.run(make_client().update())

    assert panel.weather == Weather(
        temperature=14.2, weather_code=2, is_day=True, high=18.4, low=9.1, precipitation_probability=40
    )
    geocode_request, forecast_request = (call.request for call in responses.calls)
    assert "name=London" in geocode_request.url
    assert "latitude=51.5" in forecast_request.url
    assert "longitude=-0.12" in forecast_request.url


@responses.activate
def test_forecast_can_lack_a_precipitation_probability():
    forecast = {**FORECAST_JSON, "daily": {**FORECAST_JSON["daily"], "precipitation_probability_max": [None]}}
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)
    responses.add(responses.GET, FORECAST_URL, json=forecast)

    assert asyncio.run(make_client().update()).weather.precipitation_probability is None


@responses.activate
def test_no_location_configured_shows_the_weather_at_the_north_pole_without_geocoding():
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)

    panel = asyncio.run(make_client(location="").update())

    assert panel.weather.temperature == 14.2
    (forecast_request,) = (call.request for call in responses.calls)
    assert "latitude=90.0" in forecast_request.url
    assert "longitude=0.0" in forecast_request.url


@responses.activate
def test_unknown_location_says_so():
    responses.add(responses.GET, GEOCODING_URL, json={"generationtime_ms": 0.5})  # no "results" key

    assert asyncio.run(make_client("Nowheresville").update()).message == "Location not found"
    assert len(responses.calls) == 1  # never got as far as asking for a forecast


@responses.activate
def test_repeated_calls_within_refresh_interval_reuse_the_panel():
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)
    client = make_client()

    first = asyncio.run(client.update())
    second = asyncio.run(client.update())

    assert second is first
    assert len(responses.calls) == 2


@responses.activate
def test_unknown_location_is_not_retried_every_cycle():
    responses.add(responses.GET, GEOCODING_URL, json={})
    client = make_client("Nowheresville")

    asyncio.run(client.update())
    asyncio.run(client.update())

    assert len(responses.calls) == 1


@responses.activate
def test_refetches_after_refresh_interval_without_geocoding_again(monkeypatch):
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)
    client = make_client()
    asyncio.run(client.update())
    client._last_attempt -= weather_client.REFRESH_INTERVAL + dt.timedelta(seconds=1)

    asyncio.run(client.update())

    urls = [call.request.url for call in responses.calls]
    assert sum(url.startswith(GEOCODING_URL) for url in urls) == 1
    assert sum(url.startswith(FORECAST_URL) for url in urls) == 2


@responses.activate
def test_failed_fetch_raises_and_is_retried_next_cycle():
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)
    responses.add(responses.GET, FORECAST_URL, status=500)
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)
    client = make_client()

    with pytest.raises(requests.exceptions.RequestException):
        asyncio.run(client.update())

    assert asyncio.run(client.update()).weather.temperature == 14.2


def test_needs_refresh_only_when_the_location_changes():
    client = WeatherClient(REGISTRATION, WeatherConfig(api_key="old", location="London"))

    assert not client.needs_refresh(WeatherConfig(api_key="new", location="London"))  # keyless API
    assert client.needs_refresh(WeatherConfig(api_key="old", location="Paris"))


@pytest.mark.parametrize(
    ("location", "url"),
    [
        ("SE17 2PX", "https://api.postcodes.io/postcodes/SE17 2PX"),
        ("se172px", "https://api.postcodes.io/postcodes/se172px"),
        ("SE17", "https://api.postcodes.io/outcodes/SE17"),
    ],
)
@responses.activate
def test_uk_postcodes_are_located_with_postcodes_io(location, url):
    responses.add(
        responses.GET,
        url,
        json={"result": {"postcode": "SE17 2PX", "latitude": 51.48, "longitude": -0.09, "country": "England"}},
    )
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)

    assert asyncio.run(make_client(location).update()).weather.temperature == 14.2
    assert not any(call.request.url.startswith(GEOCODING_URL) for call in responses.calls)
    assert "latitude=51.48" in responses.calls[-1].request.url


@responses.activate
def test_unknown_postcode_says_location_not_found():
    responses.add(responses.GET, "https://api.postcodes.io/postcodes/ZZ9 9ZZ", status=404)

    assert asyncio.run(make_client("ZZ9 9ZZ").update()).message == "Location not found"


@pytest.mark.parametrize("location", ["Kennington", "Kennington, London", "SE17 2PX London"])
def test_only_postcode_shaped_text_counts_as_a_postcode(location):
    assert not is_postcode(location)
