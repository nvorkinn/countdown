"""The floods provider: the Environment Agency's warnings near each UK postcode in `v1:sub:postcode`.

Each is written to `v1:notices:floods:<place>` (keys.floods) as the EA's own answer, which the notice board makes its
notices from. The EA covers England; a postcode elsewhere just has nothing to report. Norwegian postcodes (four
digits) are in the set for Norway's notices, and are skipped.

One fetch serves every postcode. The EA's /id/floods lists every warning in force in England, and its documentation
asks callers to keep to such common requests, which it caches, rather than filter each its own way. So a cycle asks
for that list once, and each postcode gets the warnings for the flood areas within RADIUS_KM of it, as the EA lists
them (/id/floodAreas?lat&long&dist): the areas /id/floods?lat&long&dist would pick for it. Where a postcode is
comes from the store, as provider-locations placed it (`v1:location:<place>`, read each cycle by
locations.reader.LocationReader; one it hasn't placed, or that doesn't exist, is left out until it has), and its flood
areas are asked for again once a day, as the EA redraws them now and then, or at once if it's moved.

Nothing logged names a postcode or anything worked out from one (its district, its coordinates, a URL with either in
it): a postcode says where a screen, and someone's home, is, and the logs go to the central log store. So a failure is
logged by its reason (log_format.error_reason: the error's type, and an HTTP error's status); urllib3's warnings, which
quote the URL they retried, are kept out of every process's logs (countdown_core.core.log_format), and the runner names
no postcode either.

The HTTP calls are synchronous (requests), so each batch runs in a worker thread (asyncio.to_thread), like the TfL
provider's.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from countdown_core.core.http_session import DEFAULT_TIMEOUT, retrying_session
from countdown_core.core.log_format import configure_logging, error_reason
from countdown_core.notices.location import FULL_POSTCODE
from countdown_core.notices.models import FloodAreasResponse, FloodWarningsResponse
from countdown_core.store import keys
from countdown_core.store.floods import StoredFloods
from countdown_providers.config import Settings
from countdown_providers.locations.reader import LocationReader
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

    from countdown_core.tfl.models import Postcode

logger = logging.getLogger(__name__)

FLOODS_URL = "https://environment.data.gov.uk/flood-monitoring/id/floods"
FLOOD_AREAS_URL = "https://environment.data.gov.uk/flood-monitoring/id/floodAreas"
# How near a flood area must be for its warnings to be shown.
RADIUS_KM = 5
# /id/floodAreas returns 500 areas unless asked for more; ask for more than RADIUS_KM takes in anywhere.
FLOOD_AREAS_LIMIT = 2000
# How long a postcode's flood areas are used before they're asked for again.
FLOOD_AREAS_REFRESH = timedelta(days=1)
# The runner fetches ten postcodes at a time: one list of the warnings in force serves all of a cycle's batches. Well
# under interval_s, so the next tick's cycle asks afresh; one woken sooner (wakes_on) uses the last one's.
IN_FORCE_REUSE = timedelta(minutes=2)


def fetch_in_force(session: requests.Session) -> FloodWarningsResponse:
    """Fetch every flood warning in force in England. Raises if the EA can't be reached or read."""
    response = session.get(FLOODS_URL, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return FloodWarningsResponse.model_validate_json(response.content)


def fetch_flood_areas(session: requests.Session, latitude: float, longitude: float) -> frozenset[str]:
    """Fetch the ids of the flood areas within RADIUS_KM of a place. Raises if the EA can't be reached or read."""
    params = {"lat": latitude, "long": longitude, "dist": RADIUS_KM, "_limit": FLOOD_AREAS_LIMIT}
    response = session.get(FLOOD_AREAS_URL, params=params, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return frozenset(area.notation for area in FloodAreasResponse.model_validate_json(response.content).items)


class FloodsProvider:
    """Flood warnings near the subscribed UK postcodes, from the Environment Agency (keyless, so no API key)."""

    name = keys.POSTCODE
    ttl_s = 30 * 60
    # The EA updates its warnings every 15 minutes; asking every 5 shows a change within 5 minutes of it.
    interval_s = 5 * 60
    # A cycle as soon as provider-locations places a new postcode (or moves one), not at the next tick.
    wakes_on = (keys.LOCATION_CHANGED,)

    def __init__(
        self,
        session: requests.Session,
        locations: LocationReader,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Ask the EA through `session`, and the store where each postcode is through `locations`.

        `now` stamps fetched_at (a parameter for the tests).
        """
        self.session = session
        self.locations = locations
        self.now = now
        # Postcode -> (when they were asked for, where it was then, and the ids of the flood areas within RADIUS_KM of
        # it). Only grows (a few hundred bytes each), like provider-locations' own.
        self.areas: dict[str, tuple[datetime, tuple[float, float], frozenset[str]]] = {}
        # When the warnings in force were last asked for, and what they were (None if the EA couldn't be asked).
        self.in_force: tuple[datetime, FloodWarningsResponse | None] | None = None

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:floods:<place>`."""
        return keys.floods(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredFloods]:
        """Fetch the warnings near each UK postcode, leaving out (and logging) any that fail or that can't be placed."""
        # Anything else (a Norwegian postcode) is another provider's.
        postcodes = [postcode for postcode in ids if FULL_POSTCODE.fullmatch(postcode)]
        places = await self.locations.places(postcodes, notices="flood warnings")
        if not places:
            return {}
        # One thread for the whole batch, one postcode after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, places)

    def _fetch_blocking(self, places: dict[str, Postcode]) -> dict[str, StoredFloods]:
        fetched_at, in_force = self._in_force()
        if in_force is None:
            return {}
        results: dict[str, StoredFloods] = {}
        for postcode, place in places.items():
            try:
                areas = self._areas_near(postcode, (place.latitude, place.longitude))
            except Exception as e:  # noqa: BLE001 -- one bad postcode mustn't lose the rest of the batch
                # Not the error's own text: an HTTP error's quotes the URL, with the postcode's coordinates in it.
                logger.warning("Couldn't fetch the flood areas near a subscribed postcode: %s", error_reason(e))
                continue
            items = [warning for warning in in_force.items if warning.flood_area_id in areas]
            results[postcode] = StoredFloods(
                fetched_at=fetched_at, postcode=postcode, floods=FloodWarningsResponse(items=items)
            )
        return results

    def _in_force(self) -> tuple[datetime, FloodWarningsResponse | None]:
        """Return when the warnings in force were fetched, and what they were (None if the EA couldn't be asked).

        Once a cycle: a later batch gets what the first one fetched, or didn't.
        """
        now = self.now()
        if self.in_force is None or now - self.in_force[0] >= IN_FORCE_REUSE:
            try:
                answer = fetch_in_force(self.session)
            except Exception as e:  # noqa: BLE001 -- not raised: a later batch this cycle mustn't ask again
                logger.warning("Couldn't fetch the flood warnings in force: %s", e)  # its URL names no place
                answer = None
            self.in_force = (now, answer)
        return self.in_force

    def _areas_near(self, postcode: str, coordinates: tuple[float, float]) -> frozenset[str]:
        """Return the ids of the flood areas near `postcode`, at `coordinates`; raises if the EA can't be asked.

        Asked for once a day, and again at once if provider-locations has moved the postcode since.
        """
        now = self.now()
        cached = self.areas.get(postcode)
        if cached is not None and cached[1] == coordinates and now - cached[0] < FLOOD_AREAS_REFRESH:
            return cached[2]
        areas = fetch_flood_areas(self.session, *coordinates)
        self.areas[postcode] = (now, coordinates, areas)
        return areas


def main() -> None:
    """Run the `countdown-provider-floods` console script."""
    configure_logging()
    settings = Settings.from_env()
    # One client for the runner and the locations it reads.
    redis = settings.redis()
    # countdown_core's retrying session: retries connection errors and 502/503/504 with backoff.
    session = retrying_session()
    asyncio.run(serve(FloodsProvider(session, LocationReader(redis)), settings, redis=redis))


if __name__ == "__main__":
    main()
