"""The weather panel's client: the forecast for the configured location, from the store."""

import datetime as dt
import logging
from typing import TYPE_CHECKING, override

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


class WeatherClient(AbstractClient):
    """A forecast carousel, read from the store under the location as the broker subscribes to it.

    That's `v1:weather:<location>`, written by the weather provider from Open-Meteo (countdown_core.weather.open_meteo),
    which is keyless, so the config's api_key is unused. With no location configured there's nothing to read, and the
    panel says it needs configuring.
    """

    panel_title = N_("Weather")

    def __init__(self, registration: Registration, config: WeatherSection) -> None:
        """`config` is the broker's weather section: the place, or postcode, to forecast for. Raises without a store."""
        super().__init__(registration, config)
        self.store = reader.require_store()
        self.location = config.location
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
        """Return the next page of the forecast, or a MessagePanel if no location is configured.

        A forecast missing from the store, or Redis failing, raises, so the registry keeps the last good panel.
        """
        if not self.location:
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
            self._panel = WeatherPanel(self._pages[self._next_page])
            self._next_page = (self._next_page + 1) % len(self._pages)
        return self._panel

    def _use(self, forecast: ForecastResponse) -> None:
        self._pages = weather_pages(forecast)
        self._utc_offset = dt.timedelta(seconds=forecast.utc_offset_seconds)

    def _stored_forecast(self) -> ForecastResponse:
        """Read the location's forecast from the store; raises if there's none, so the registry keeps the last panel."""
        stored = reader.read(self.store, keys.weather(self.location), StoredForecast)
        if stored is None:
            msg = f"No forecast in the store for {self.location!r}"
            raise NoStoredForecastError(msg)
        return stored.forecast
