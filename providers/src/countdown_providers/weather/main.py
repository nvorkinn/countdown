"""The weather provider: Open-Meteo's forecast for every subscribed place, written to `v1:weather:<location key>`.

Location keys come from the broker's place picker: `<name>-<country>-<GeoNames id>`, e.g. `london-gb-2643743`, so the
id, last, finds the place exactly (Open-Meteo's get-by-id, once per place). Typed text, which the broker slugs
(`london`, `sw1a-1aa`), is geocoded by name instead (Open-Meteo's search, once per place). Only a number after a
two-letter country is an id, so a slug that ends in one (`oslo-0150`) is a name.

Except a slug of a UK postcode or postcode district (`sw1a-1aa`, `se17`: store.location.weather_postcode), which this
doesn't look up: provider-locations does, as the one process that looks postcodes up, and writes where it is to
`v1:location:<place>`, which this reads every cycle (locations.reader.LocationReader, on the runner's own Redis
client). One it hasn't placed yet, or that doesn't exist, has no forecast that cycle, as for the providers of notices
about a place; a forecast already stored for it stays until its TTL.

It uses countdown_core.weather.open_meteo's HTTP calls, which are synchronous (requests), so each batch runs in
a worker thread (asyncio.to_thread), like the TfL provider.

A location key says where a screen is (a town, or typed text, which can be a postcode), and the logs go to the central
log store, so nothing logged here names one, nor a URL with its name or coordinates in it: a failure is logged by its
reason (log_format.error_reason). Which places have no forecast is in Redis: the keys in `v1:sub:weather` without a
`v1:weather:` key.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from countdown_core.core.http_session import retrying_session
from countdown_core.core.log_format import configure_logging, error_reason
from countdown_core.store import keys
from countdown_core.store.location import weather_postcode
from countdown_core.store.models import StoredForecast
from countdown_core.weather.open_meteo import fetch_forecast, geocode, place_coordinates
from countdown_providers.config import Settings
from countdown_providers.locations.reader import LocationReader
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

logger = logging.getLogger(__name__)

# A picked place's key ends in its country code (ISO 3166-1 alpha-2, lowercase) and GeoNames id: `...-gb-2643743`.
_PLACE_ID = re.compile(r"(?:^|-)[a-z]{2}-([0-9]+)\Z")


def place_id_of(location: str) -> int | None:
    """Return the GeoNames id at the end of a location key (`london-gb-2643743` -> 2643743), or None if it has none.

    A key without a country before its last number is a slug of typed text (`oslo-0150`, `0150`), not a place's.
    """
    match = _PLACE_ID.search(location)
    return int(match[1]) if match else None


class WeatherProvider:
    """Weather for the subscribed places, from Open-Meteo (keyless, so no API key)."""

    name = keys.WEATHER
    ttl_s = 30 * 60
    # Open-Meteo updates its forecasts about hourly; ten minutes keeps "now" close without hammering it.
    interval_s = 10 * 60
    # A cycle as soon as provider-locations places a typed postcode (or moves one), not at the next tick.
    wakes_on = (keys.LOCATION_CHANGED,)

    def __init__(
        self,
        session: requests.Session,
        locations: LocationReader,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Ask Open-Meteo through `session`, and the store where a typed postcode is through `locations`.

        `now` stamps fetched_at (a parameter for the tests).
        """
        self.session = session
        self.locations = locations
        self.now = now
        # Location key -> (latitude, longitude), for the places looked up here (not the postcodes, read each cycle, so
        # one provider-locations moves is moved here too). Places don't move, so this only grows (a few dozen bytes
        # each).
        self.coordinates: dict[str, tuple[float, float]] = {}

    def output_key(self, item_id: str) -> str:
        """Write to `v1:weather:<location key>`."""
        return keys.weather(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredForecast]:
        """Fetch each place's forecast, leaving out (and logging) any that fail or that can't be placed.

        A typed postcode is placed from the store; one it can't place is left out, and only counted in the log.
        """
        postcodes = {location: postcode for location in ids if (postcode := weather_postcode(location)) is not None}
        placed = await self.locations.places(sorted(set(postcodes.values())), notices="weather")
        stored = {
            location: (place.latitude, place.longitude)
            for location, postcode in postcodes.items()
            if (place := placed.get(postcode)) is not None
        }
        wanted = [location for location in ids if location not in postcodes or location in stored]
        # One thread for the whole batch, one place after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, wanted, stored)

    def _fetch_blocking(self, ids: list[str], stored: dict[str, tuple[float, float]]) -> dict[str, StoredForecast]:
        """Fetch the forecasts for `ids`, placing those in `stored` there, and looking the rest up."""
        results: dict[str, StoredForecast] = {}
        for location in ids:
            try:
                forecast = self._fetch_place(location, stored.get(location))
            except Exception as e:  # noqa: BLE001 -- one bad place mustn't lose the rest of the batch
                logger.warning("Couldn't fetch the weather for a subscribed place: %s", error_reason(e))
                continue
            if forecast is not None:
                results[location] = forecast
        return results

    def _fetch_place(self, location: str, coordinates: tuple[float, float] | None) -> StoredForecast | None:
        """Fetch the forecast at `coordinates`, or (None) where the place is, looked up here."""
        if coordinates is None:
            coordinates = self._coordinates(location)
        if coordinates is None:
            logger.warning("Open-Meteo can't find a subscribed place, so it has no weather")
            return None
        fetched_at = self.now()
        forecast = fetch_forecast(self.session, *coordinates)
        return StoredForecast(fetched_at=fetched_at, location=location, forecast=forecast)

    def _coordinates(self, location: str) -> tuple[float, float] | None:
        """Look the place up once: by its GeoNames id when the key has one, else by its (slugged) name.

        Never a typed postcode's (fetch places those from the store): only provider-locations looks postcodes up.
        """
        if location not in self.coordinates:
            place_id = place_id_of(location)
            if place_id is not None:
                found = place_coordinates(self.session, place_id)
            else:
                # Not cached when nothing matches, so a place Open-Meteo learns about later is picked up.
                found = geocode(self.session, location.replace("-", " "))
            if found is None:
                return None
            self.coordinates[location] = found
        return self.coordinates[location]


def main() -> None:
    """Run the `countdown-provider-weather` console script."""
    configure_logging()
    settings = Settings.from_env()
    # One client for the runner and the locations it reads.
    redis = settings.redis()
    # countdown_core's retrying session: retries connection errors and 502/503/504 with backoff.
    session = retrying_session()
    asyncio.run(serve(WeatherProvider(session, LocationReader(redis)), settings, redis=redis))


if __name__ == "__main__":
    main()
