"""The weather panel's client: the forecast from Open-Meteo, for the configured location."""

import datetime as dt
import logging
from typing import TYPE_CHECKING, override

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.i18n import N_
from countdown_core.notices.location import is_postcode, postcode_coordinates
from countdown_core.store import keys, reader
from countdown_core.store.models import StoredForecast
from countdown_core.weather.models import ForecastResponse, GeocodingResponse, GeocodingResult, Weather
from countdown_core.weather.weather_forecast import weather_pages
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    import redis
    import requests

    from countdown_api.models import Weather as WeatherSection
    from countdown_core.core.panel import Panel
    from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
# One place by its GeoNames id, as the config page's place picker stores it.
PLACE_URL = "https://geocoding-api.open-meteo.com/v1/get"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Where the weather is for until a location is set.
NORTH_POLE = (90.0, 0.0)

# Forecasts move slowly.
REFRESH_INTERVAL = dt.timedelta(minutes=10)


class NoStoredForecastError(LookupError):
    """The store has no forecast for the location: the weather provider is down, or hasn't fetched it yet."""


class WeatherClient(AbstractClient):
    """A forecast carousel from Open-Meteo, which is keyless, so the config's api_key is unused.

    The location is geocoded once, on first fetch; with no location configured it's the weather at the North Pole.
    When the renderer reads the store (countdown_core.store.reader), the forecast comes from there instead, under the
    location as the broker subscribes to it (`v1:weather:<location>`), and Open-Meteo isn't asked at all.
    """

    panel_title = N_("Weather")

    def __init__(self, registration: Registration, config: WeatherSection) -> None:
        """`config` is the broker's weather section: the place, or postcode, to forecast for."""
        super().__init__(registration, config)
        self.location = config.location
        self._coordinates: tuple[float, float] | None = None
        self._panel: Panel | None = None
        self._last_attempt: dt.datetime | None = None
        self._utc_offset = dt.timedelta(0)
        self._pages: tuple[Weather, ...] = ()
        self._next_page = 0

    @override
    def _initialise(self) -> None:
        pass

    @override
    def needs_refresh(self, new_config: WeatherSection) -> bool:
        # Open-Meteo is keyless, so api_key changing changes nothing.
        return self.location != new_config.location

    @override
    def _update(self) -> Panel:
        """Return the next page of the forecast, or a MessagePanel if Open-Meteo can't find the location.

        Network and parse failures raise, so the registry keeps the last good panel.
        """
        now = dt.datetime.now(dt.UTC)
        elapsed = now - self._last_attempt if self._last_attempt is not None else None
        date_changed = (
            bool(self._pages)
            and self._last_attempt is not None
            and (now + self._utc_offset).date() != (self._last_attempt + self._utc_offset).date()
        )
        if elapsed is None or elapsed >= REFRESH_INTERVAL or date_changed:
            store = reader.store()
            if store is not None and self.location:
                self._use(self._stored_forecast(store))
            else:
                self._fetch_directly()
            self._last_attempt = now

        if self._pages:
            self._panel = WeatherPanel(self._pages[self._next_page])
            self._next_page = (self._next_page + 1) % len(self._pages)
        return self._panel

    def _fetch_directly(self) -> None:
        """Geocode the location (once) and ask Open-Meteo for its forecast, as a renderer without the store does."""
        coordinates = self._geocode() if self.location else NORTH_POLE
        if coordinates is None:
            logger.warning("Could not find a location matching %r", self.location)
            self._panel = self.message_panel(N_("Location not found"))
            self._pages = ()
        else:
            self._use(self._fetch_forecast(*coordinates))

    def _use(self, forecast: ForecastResponse) -> None:
        self._pages = weather_pages(forecast)
        self._utc_offset = dt.timedelta(seconds=forecast.utc_offset_seconds)

    def _stored_forecast(self, store: redis.Redis) -> ForecastResponse:
        """Read the location's forecast from the store; raises if there's none, so the registry keeps the last panel."""
        stored = reader.read(store, keys.weather(self.location), StoredForecast)
        if stored is None:
            msg = f"No forecast in the store for {self.location!r}"
            raise NoStoredForecastError(msg)
        return stored.forecast

    def _geocode(self) -> tuple[float, float] | None:
        if self._coordinates is None:
            self._coordinates = geocode(self.session, self.location)
        return self._coordinates

    def _fetch_forecast(self, latitude: float, longitude: float) -> ForecastResponse:
        return fetch_forecast(self.session, latitude, longitude)


# The HTTP calls themselves, shared by WeatherClient and the weather provider (countdown_providers.weather).


def geocode(session: requests.Session, location: str) -> tuple[float, float] | None:
    """Return the coordinates of a place name or UK postcode; None if nothing matches, raises if it can't ask.

    Open-Meteo's geocoder only knows place names, so UK postcodes go to postcodes.io.
    """
    if is_postcode(location):
        return postcode_coordinates(session, location)
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
