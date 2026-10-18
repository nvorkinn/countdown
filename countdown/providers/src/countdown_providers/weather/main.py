"""The weather provider: Open-Meteo's forecast for every subscribed place, written to `v1:weather:<location key>`.

Location keys come from the broker's place picker: `<name>-<country>-<GeoNames id>`, e.g. `london-gb-2643743`, so the
id, last, finds the place exactly (Open-Meteo's get-by-id, once per place). A screen saved before the picker has only
its typed text, which the broker slugs (`london`, `sw1a-1aa`) until it's backfilled; those are geocoded by name
instead. Only a number after a two-letter country is an id, so a slug that ends in one (`oslo-0150`) is a name.

It uses countdown_core.weather.open_meteo's HTTP calls, which are synchronous (requests), so each batch runs in
a worker thread (asyncio.to_thread), like the TfL provider.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import AbstractClient
from countdown_core.core.log_format import configure_logging
from countdown_core.store import keys
from countdown_core.store.models import StoredForecast
from countdown_core.weather.open_meteo import fetch_forecast, geocode, place_coordinates
from countdown_providers.config import Settings
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

    def __init__(self, session: requests.Session, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask Open-Meteo through `session`; `now` stamps fetched_at (a parameter for the tests)."""
        self.session = session
        self.now = now
        # Location key -> (latitude, longitude). Places don't move, so this only grows (a few dozen bytes each).
        self.coordinates: dict[str, tuple[float, float]] = {}

    def output_key(self, item_id: str) -> str:
        """Write to `v1:weather:<location key>`."""
        return keys.weather(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredForecast]:
        """Fetch each place's forecast, leaving out (and logging) any that fail or that Open-Meteo can't find."""
        # One thread for the whole batch, one place after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredForecast]:
        results: dict[str, StoredForecast] = {}
        for location in ids:
            try:
                forecast = self._fetch_place(location)
            except Exception as e:  # noqa: BLE001 -- one bad place mustn't lose the rest of the batch
                logger.warning("Couldn't fetch the weather for %s: %s", location, e)
                continue
            if forecast is not None:
                results[location] = forecast
        return results

    def _fetch_place(self, location: str) -> StoredForecast | None:
        coordinates = self._coordinates(location)
        if coordinates is None:
            logger.warning("Open-Meteo can't find a place for %r", location)
            return None
        fetched_at = self.now()
        forecast = fetch_forecast(self.session, *coordinates)
        return StoredForecast(fetched_at=fetched_at, location=location, forecast=forecast)

    def _coordinates(self, location: str) -> tuple[float, float] | None:
        """Look the place up once: by its GeoNames id when the key has one, else by its (slugged) name."""
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
    # The renderer's own retrying session: retries connection errors and 502/503/504 with backoff.
    session = AbstractClient._build_retrying_session()  # noqa: SLF001 -- shared with the renderer's clients on purpose
    asyncio.run(serve(WeatherProvider(session), Settings.from_env()))


if __name__ == "__main__":
    main()
