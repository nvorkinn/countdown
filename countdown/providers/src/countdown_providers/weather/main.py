"""The weather provider: only the shape so far. It runs, but fetches (and writes) nothing.

TODO: add a stored weather model to countdown_core.store.models, then fetch each location (keys like
`london-gb-2643743`, the GeoNames id last) from Open-Meteo, reusing countdown_core.weather.weather_client the way
tfl/main.py reuses TflSource.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from countdown_core.core.log_format import configure_logging
from countdown_core.store import keys
from countdown_providers.config import Settings
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)


class WeatherProvider:
    """Weather for the subscribed locations. A stub: fetch() returns nothing yet."""

    name = keys.WEATHER
    ttl_s = 30 * 60
    interval_s = 10 * 60

    def output_key(self, item_id: str) -> str:
        """Write to `v1:weather:<location key>`."""
        return keys.weather(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, BaseModel]:
        """Fetch nothing (TODO), so nothing is written and the weather panels stay hidden."""
        logger.debug("Weather isn't implemented yet; skipping %d locations", len(ids))
        return {}


def main() -> None:
    """Run the `countdown-provider-weather` console script."""
    configure_logging()
    logger.warning("The weather provider is a stub: it writes nothing yet")
    asyncio.run(serve(WeatherProvider(), Settings.from_env()))


if __name__ == "__main__":
    main()
