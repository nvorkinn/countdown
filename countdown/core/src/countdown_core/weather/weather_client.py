"""The weather panel's client: the forecast for the configured location, from the store."""

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
    from countdown_core.weather.models import ForecastResponse
    from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)


class NoStoredForecastError(reader.NoStoredDataError):
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
    (countdown_core.weather.open_meteo), which is keyless, so the config carries no key. With no location
    configured there's nothing to read, and the panel says it needs configuring. The forecast is read on every poll
    and nothing of it is kept: once its key has expired, the panel says it's not available.
    """

    panel_title = N_("Weather")

    def __init__(self, registration: Registration, config: WeatherSection) -> None:
        """`config` is the broker's weather section: the place to forecast for. Raises without a store."""
        super().__init__(registration, config)
        self.store = reader.require_store()
        self.location = config.location
        self.key = location_key(config)

    @override
    def _initialise(self) -> None:
        pass

    @override
    def needs_refresh(self, new_config: WeatherSection) -> bool:
        # The key changes with the place, and when a broker starts sending it; location too, as the forecast falls
        # back to it (see _stored_forecast).
        return (self.key, self.location) != (location_key(new_config), new_config.location)

    @override
    def _update(self) -> Panel:
        """Return this minute's page (core.rotation) of the forecast the store has now; a MessagePanel without a place.

        A forecast missing from the store raises NoStoredForecastError, so the panel says it's not available; Redis
        failing raises too, and the registry keeps the last panel.
        """
        if not self.key:
            return self.message_panel(N_("Needs configuring"))
        pages = weather_pages(self._stored_forecast())
        return WeatherPanel(pages[rotation.page(len(pages))])

    def _stored_forecast(self) -> ForecastResponse:
        """Read the place's forecast from the store; raises NoStoredForecastError if there's none.

        Not there under its key, it's read under the location's text, which is what a broker subscribed before it
        sent keys: just after one starts to, that forecast is still in the store (until its TTL) while the provider
        hasn't yet fetched the key's.
        """
        stored = reader.read(self.store, keys.weather(self.key), StoredForecast)
        if stored is None and self.location and self.location != self.key:
            stored = reader.read(self.store, keys.weather(self.location), StoredForecast)
        if stored is None:
            # Not naming the key: this is logged (AbstractClient.update), and the key says where the screen is (a town,
            # or typed text, which can be a postcode).
            msg = "No forecast in the store for the screen's place"
            raise NoStoredForecastError(msg)
        return stored.forecast
