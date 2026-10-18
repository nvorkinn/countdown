"""The weather panel's client: the forecast for the configured location, from the store."""

import datetime as dt
import logging
from typing import TYPE_CHECKING, override

from countdown_core.core import rotation
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.i18n import N_
from countdown_core.store import keys, reader
from countdown_core.store.models import StoredForecast
from countdown_core.weather.weather_forecast import weather_pages
from countdown_core.weather.weather_panel import WeatherPanel

if TYPE_CHECKING:
    from countdown_api.models import Weather as WeatherSection
    from countdown_core.core.panel import Panel
    from countdown_core.weather.models import ForecastResponse, Weather
    from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)

# Forecasts move slowly.
REFRESH_INTERVAL = dt.timedelta(minutes=10)


class NoStoredForecastError(LookupError):
    """The store has no forecast for the location: the weather provider is down, or hasn't fetched it yet."""


def location_key(config: WeatherSection) -> str:
    """Return the key the broker subscribes the screen's weather under, e.g. "london-gb-2643743"; "" for none.

    That's the config's `key`: the picked place's name, country and Open-Meteo id. A broker that predates `key` sends
    null, and subscribes the text in `location` instead.
    """
    return config.key or config.location


class WeatherClient(AbstractClient):
    """A forecast carousel, read from the store under the location key the broker subscribes to.

    That's `v1:weather:<key>` (see location_key), written by the weather provider from Open-Meteo
    (countdown_core.weather.open_meteo), which is keyless, so the config's api_key is unused. With no location
    configured there's nothing to read, and the panel says it needs configuring.
    """

    panel_title = N_("Weather")

    def __init__(self, registration: Registration, config: WeatherSection) -> None:
        """`config` is the broker's weather section: the place to forecast for. Raises without a store."""
        super().__init__(registration, config)
        self.store = reader.require_store()
        self.location = config.location
        self.key = location_key(config)
        self._panel: Panel | None = None
        self._last_attempt: dt.datetime | None = None
        self._utc_offset = dt.timedelta(0)
        self._pages: tuple[Weather, ...] = ()

    @override
    def _initialise(self) -> None:
        pass

    @override
    def needs_refresh(self, new_config: WeatherSection) -> bool:
        # Open-Meteo is keyless, so api_key changing changes nothing. The key changes with the place, and when a
        # broker starts sending it; location too, as the forecast falls back to it (see _stored_forecast).
        return (self.key, self.location) != (location_key(new_config), new_config.location)

    @override
    def _update(self) -> Panel:
        """Return this minute's page of the forecast (core.rotation), or a MessagePanel if there's no location.

        A forecast missing from the store, or Redis failing, raises, so the registry keeps the last good panel.
        """
        if not self.key:
            return self.message_panel(N_("Needs configuring"))
        now = dt.datetime.now(dt.UTC)
        elapsed = now - self._last_attempt if self._last_attempt is not None else None
        date_changed = (
            bool(self._pages)
            and self._last_attempt is not None
            and (now + self._utc_offset).date() != (self._last_attempt + self._utc_offset).date()
        )
        if elapsed is None or elapsed >= REFRESH_INTERVAL or date_changed:
            self._use(self._stored_forecast())
            self._last_attempt = now

        if self._pages:
            self._panel = WeatherPanel(self._pages[rotation.page(len(self._pages))])
        return self._panel

    def _use(self, forecast: ForecastResponse) -> None:
        self._pages = weather_pages(forecast)
        self._utc_offset = dt.timedelta(seconds=forecast.utc_offset_seconds)

    def _stored_forecast(self) -> ForecastResponse:
        """Read the place's forecast from the store; raises if there's none, so the registry keeps the last panel.

        Not there under its key, it's read under the location's text, which is what a broker subscribed before it
        sent keys: just after one starts to, that forecast is still in the store (until its TTL) while the provider
        hasn't yet fetched the key's.
        """
        stored = reader.read(self.store, keys.weather(self.key), StoredForecast)
        if stored is None and self.location and self.location != self.key:
            stored = reader.read(self.store, keys.weather(self.location), StoredForecast)
        if stored is None:
            # Not naming the key: the registry logs this, and the key says where the screen is (a town, or typed text,
            # which can be a postcode).
            msg = "No forecast in the store for the screen's place"
            raise NoStoredForecastError(msg)
        return stored.forecast
