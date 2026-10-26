"""Open-Meteo's geocoding and forecast calls, which the weather provider makes (test_weather_provider has the rest)."""

from urllib.parse import parse_qs, urlparse

import requests
import responses
from weather_factory import forecast_json

from countdown_core.weather.open_meteo import FORECAST_URL, GEOCODING_URL, fetch_forecast, geocode

GEOCODING_JSON = {"results": [{"name": "London", "latitude": 51.5, "longitude": -0.12, "country": "United Kingdom"}]}


@responses.activate
def test_geocode_asks_for_the_place_by_name():
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)

    assert geocode(requests.Session(), "London") == (51.5, -0.12)
    assert "name=London" in responses.calls[0].request.url


@responses.activate
def test_an_unknown_place_is_none():
    responses.add(responses.GET, GEOCODING_URL, json={"generationtime_ms": 0.5})  # no "results" key

    assert geocode(requests.Session(), "Nowheresville") is None


@responses.activate
def test_the_forecast_asks_for_eight_days_of_what_the_pages_draw():
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())

    assert fetch_forecast(requests.Session(), 51.5, -0.12).current.temperature == 14.2

    url = responses.calls[0].request.url
    assert "latitude=51.5" in url
    assert "longitude=-0.12" in url
    query = parse_qs(urlparse(url).query)
    assert query["forecast_days"] == ["8"]
    assert {"temperature_2m", "apparent_temperature", "precipitation_probability"} <= set(query["hourly"][0].split(","))
    assert "wind_speed_10m_max" in query["daily"][0].split(",")


@responses.activate
def test_geocode_asks_open_meteo_even_for_postcode_shaped_text():
    """Only provider-locations looks postcodes up; the weather provider reads typed ones from the store."""
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)

    assert geocode(requests.Session(), "SE17 2PX") == (51.5, -0.12)
    assert [call.request.url.split("?")[0] for call in responses.calls] == [GEOCODING_URL]
