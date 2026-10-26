"""Open-Meteo's geocoding and forecast, as the weather provider (countdown_providers.weather) asks for them.

The renderer doesn't call these: it reads the forecasts the provider stores (WeatherClient).
"""

from typing import TYPE_CHECKING

from countdown_shared.core.http_session import DEFAULT_TIMEOUT
from countdown_shared.weather.models import ForecastResponse, GeocodingResponse, GeocodingResult

if TYPE_CHECKING:
    import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
# One place by its GeoNames id, as the config page's place picker stores it.
PLACE_URL = "https://geocoding-api.open-meteo.com/v1/get"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def geocode(session: requests.Session, location: str) -> tuple[float, float] | None:
    """Return the coordinates of the place Open-Meteo matches best to a name; None if none, raises if it can't ask.

    Not for a UK postcode: the weather provider places those from the store (provider-locations looks them up).
    """
    response = session.get(
        GEOCODING_URL, params={"name": location, "count": 1, "format": "json"}, timeout=DEFAULT_TIMEOUT
    )
    response.raise_for_status()
    results = GeocodingResponse.model_validate(response.json()).results
    return (results[0].latitude, results[0].longitude) if results else None


def place_coordinates(session: requests.Session, place_id: int) -> tuple[float, float]:
    """Return the coordinates of a place by its GeoNames id; raises if Open-Meteo doesn't know it or can't be asked."""
    response = session.get(PLACE_URL, params={"id": place_id}, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    place = GeocodingResult.model_validate(response.json())
    return place.latitude, place.longitude


def fetch_forecast(session: requests.Session, latitude: float, longitude: float) -> ForecastResponse:
    """Return Open-Meteo's forecast for a point: current conditions, hourly, and 8 days."""
    response = session.get(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,is_day,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
            "hourly": "weather_code,temperature_2m,apparent_temperature,relative_humidity_2m,"
            "wind_speed_10m,wind_direction_10m,precipitation_probability",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,"
            "sunrise,sunset,wind_speed_10m_max,wind_direction_10m_dominant",
            "timezone": "auto",  # so "today" is the location's today, not UTC's
            "forecast_days": 8,  # includes the next complete weekend even on a Sunday
        },
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return ForecastResponse.model_validate(response.json())
