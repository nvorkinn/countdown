"""Open-Meteo's geocoding and forecast calls, which the weather provider makes (test_weather_provider has the rest)."""

from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses
from weather_factory import forecast_json

from countdown_core.notices.location import is_postcode
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

    assert geocode(requests.Session(), location) == (51.48, -0.09)
    assert not any(call.request.url.startswith(GEOCODING_URL) for call in responses.calls)


@responses.activate
def test_an_unknown_postcode_is_none():
    responses.add(responses.GET, "https://api.postcodes.io/postcodes/ZZ9 9ZZ", status=404)

    assert geocode(requests.Session(), "ZZ9 9ZZ") is None


@pytest.mark.parametrize("location", ["Kennington", "Kennington, London", "SE17 2PX London"])
def test_only_postcode_shaped_text_counts_as_a_postcode(location):
    assert not is_postcode(location)
