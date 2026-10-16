import asyncio
import datetime as dt
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses
from test_utils import REGISTRATION
from weather_factory import forecast_json

from countdown_api.models import Weather as WeatherSection
from countdown_core.notices.location import is_postcode
from countdown_core.weather import weather_client
from countdown_core.weather.models import Upcoming, Weather, WeatherPeriod
from countdown_core.weather.weather_client import FORECAST_URL, GEOCODING_URL, WeatherClient

GEOCODING_JSON = {"results": [{"name": "London", "latitude": 51.5, "longitude": -0.12, "country": "United Kingdom"}]}
FORECAST_JSON = forecast_json()


def _forecast_at(time: str) -> dict:
    return {**FORECAST_JSON, "current": {**FORECAST_JSON["current"], "time": time}}


def make_client(location: str = "London") -> WeatherClient:
    return WeatherClient(REGISTRATION, WeatherSection(api_key="", location=location))


@responses.activate
def test_update_geocodes_then_fetches_forecast():
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)

    panel = asyncio.run(make_client().update())

    assert panel.weather == Weather(
        temperature=14.2,
        weather_code=2,
        is_day=True,
        high=18.4,
        low=9.1,
        precipitation_probability=40,
        temperature_units="°C",
        humidity=72,
        humidity_units="%",
        wind_speed=13.4,
        wind_speed_units="km/h",
        wind_direction=225,
        sunrise=dt.time(7, 58),
        sunset=dt.time(16, 22),
        upcoming_weather_code=(Upcoming.TONIGHT, 61),
    )
    geocode_request, forecast_request = (call.request for call in responses.calls)
    assert "name=London" in geocode_request.url
    assert "latitude=51.5" in forecast_request.url
    assert "longitude=-0.12" in forecast_request.url
    query = parse_qs(urlparse(forecast_request.url).query)
    assert query["forecast_days"] == ["8"]
    assert {"temperature_2m", "apparent_temperature", "precipitation_probability"} <= set(query["hourly"][0].split(","))
    assert "wind_speed_10m_max" in query["daily"][0].split(",")


@pytest.mark.parametrize(
    ("time", "upcoming"),
    [
        ("2026-01-15T08:30", (Upcoming.THIS_AFTERNOON, 3)),  # today at 15:00
        ("2026-01-15T12:00", (Upcoming.TONIGHT, 61)),  # today at 21:00
        ("2026-01-15T18:00", (Upcoming.TOMORROW, 73)),  # tomorrow's daily code
        ("2026-01-15T23:45", (Upcoming.TOMORROW, 73)),
    ],
)
@responses.activate
def test_the_outlook_is_for_the_next_part_of_the_day(time, upcoming):
    responses.add(responses.GET, FORECAST_URL, json=_forecast_at(time))

    assert asyncio.run(make_client(location="").update()).weather.upcoming_weather_code == upcoming


@responses.activate
def test_forecast_can_lack_a_precipitation_probability():
    forecast = {**FORECAST_JSON, "daily": {**FORECAST_JSON["daily"], "precipitation_probability_max": [None] * 8}}
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
def test_each_update_rotates_a_page_without_refetching_the_forecast():
    responses.add(responses.GET, GEOCODING_URL, json=GEOCODING_JSON)
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)
    client = make_client()

    panels = [asyncio.run(client.update()) for _ in range(6)]

    assert [panel.weather.period for panel in panels] == [
        WeatherPeriod.NOW,
        WeatherPeriod.TONIGHT,
        WeatherPeriod.TOMORROW,
        WeatherPeriod.WEEKEND,
        WeatherPeriod.NOW,
        WeatherPeriod.TONIGHT,
    ]
    assert len({id(panel) for panel in panels}) == len(panels)
    assert panels[0].weather.period is WeatherPeriod.NOW
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

    panel = asyncio.run(client.update())

    assert panel.weather.period is WeatherPeriod.TONIGHT
    urls = [call.request.url for call in responses.calls]
    assert sum(url.startswith(GEOCODING_URL) for url in urls) == 1
    assert sum(url.startswith(FORECAST_URL) for url in urls) == 2


@pytest.mark.parametrize(
    ("offset_hours", "local_time", "refresh"),
    [(-8, "23:59", True), (0, "23:59", True), (14, "23:59", True), (-8, "15:59", False), (14, "13:59", False)],
)
@responses.activate
def test_date_rollover_uses_the_weather_locations_date_without_resetting_the_carousel(
    offset_hours, local_time, refresh, monkeypatch
):
    first_forecast = forecast_json(current={"time": f"2026-01-15T{local_time}"})
    next_forecast = forecast_json(current={"time": "2026-01-16T00:01"}, start=dt.date(2026, 1, 16))
    first_forecast["utc_offset_seconds"] = next_forecast["utc_offset_seconds"] = offset_hours * 3600
    responses.add(responses.GET, FORECAST_URL, json=first_forecast)
    if refresh:
        responses.add(responses.GET, FORECAST_URL, json=next_forecast)
    clock = [
        dt.datetime.fromisoformat(f"2026-01-15T{local_time}").replace(tzinfo=dt.UTC) - dt.timedelta(hours=offset_hours)
    ]

    class FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(weather_client.dt, "datetime", FrozenDatetime)
    client = make_client(location="")
    first = asyncio.run(client.update())
    clock[0] += dt.timedelta(minutes=2)

    second = asyncio.run(client.update())

    assert first.weather.period is WeatherPeriod.NOW
    assert second.weather.period is WeatherPeriod.TONIGHT
    assert second.weather.forecast_date == dt.date(2026, 1, 16 if refresh else 15)
    assert len(responses.calls) == (2 if refresh else 1)


@pytest.mark.parametrize("failure", ["http", "misaligned", "incomplete"])
@responses.activate
def test_failed_refresh_keeps_the_last_good_frame_and_does_not_skip_a_tab(failure):
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)
    if failure == "http":
        responses.add(responses.GET, FORECAST_URL, status=500)
    else:
        broken = forecast_json()
        if failure == "misaligned":
            broken["daily"]["temperature_2m_max"].pop()
        else:
            for values in broken["daily"].values():
                del values[2:]
        responses.add(responses.GET, FORECAST_URL, json=broken)
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)
    client = make_client(location="")
    first = asyncio.run(client.update())
    client._last_attempt -= weather_client.REFRESH_INTERVAL

    with pytest.raises(requests.exceptions.RequestException if failure == "http" else ValueError):
        asyncio.run(client.update())

    assert client._panel is first
    assert asyncio.run(client.update()).weather.period is WeatherPeriod.TONIGHT


@responses.activate
def test_refreshed_forecast_data_is_used_on_the_next_tab():
    responses.add(responses.GET, FORECAST_URL, json=FORECAST_JSON)
    refreshed = forecast_json()
    refreshed["hourly"]["precipitation_probability"] = [35] * (8 * 24)
    responses.add(responses.GET, FORECAST_URL, json=refreshed)
    client = make_client(location="")
    asyncio.run(client.update())
    client._last_attempt -= weather_client.REFRESH_INTERVAL

    tonight = asyncio.run(client.update()).weather

    assert tonight.period is WeatherPeriod.TONIGHT
    assert tonight.precipitation_probability == 35


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
    client = WeatherClient(REGISTRATION, WeatherSection(api_key="old", location="London"))

    assert not client.needs_refresh(WeatherSection(api_key="new", location="London"))  # keyless API
    assert client.needs_refresh(WeatherSection(api_key="old", location="Paris"))


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
