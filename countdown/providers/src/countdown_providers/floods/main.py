"""The floods provider: the Environment Agency's warnings near each UK postcode in `v1:sub:postcode`.

Each is written to `v1:notices:floods:<place>` (keys.floods) as the EA's own answer, which the notice board makes its
notices from. The EA covers England; a postcode elsewhere just has nothing to report. Norwegian postcodes (four
digits) are in the set for Norway's notices, and are skipped.

One fetch serves every postcode. The EA's /id/floods lists every warning in force in England, and its documentation
asks callers to keep to such common requests, which it caches, rather than filter each its own way. So a cycle asks
for that list once, and each postcode gets the warnings for the flood areas within RADIUS_KM of it, as the EA lists
them (/id/floodAreas?lat&long&dist): the areas /id/floods?lat&long&dist would have picked for it. A postcode is placed
once (postcodes.io: a postcode's place doesn't change), and its flood areas are asked for again once a day, as the EA
redraws them now and then.

The logs name a postcode only by its district ("SE17"): the full postcode says where a screen, and someone's home, is.

The HTTP calls are synchronous (requests), so each batch runs in a worker thread (asyncio.to_thread), like the TfL
provider's.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.log_format import configure_logging
from countdown_core.notices.location import FULL_POSTCODE, lookup_postcode
from countdown_core.notices.models import FloodAreasResponse, FloodWarningsResponse
from countdown_core.store import keys
from countdown_core.store.floods import StoredFloods
from countdown_providers.config import Settings
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

logger = logging.getLogger(__name__)

FLOODS_URL = "https://environment.data.gov.uk/flood-monitoring/id/floods"
FLOOD_AREAS_URL = "https://environment.data.gov.uk/flood-monitoring/id/floodAreas"
# How near a flood area must be for its warnings to be shown.
RADIUS_KM = 5
# /id/floodAreas returns 500 areas unless asked for more; ask for more than RADIUS_KM takes in anywhere.
FLOOD_AREAS_LIMIT = 2000
# How long a postcode's flood areas are used before they're asked for again (and an unknown postcode looked up again).
FLOOD_AREAS_REFRESH = timedelta(days=1)
# The runner fetches ten postcodes at a time: one list of the warnings in force serves all of a cycle's batches. Well
# under interval_s, so the next cycle asks afresh.
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


def district(postcode: str) -> str:
    """Return a UK postcode's district, its outward code ("SE17 3LL" -> "SE17"): what the logs name it by."""
    return postcode.replace(" ", "")[:-3].upper()


class FloodsProvider:
    """Flood warnings near the subscribed UK postcodes, from the Environment Agency (keyless, so no API key)."""

    name = keys.POSTCODE
    ttl_s = 30 * 60
    # The EA updates its warnings every 15 minutes; asking every 5 shows a change within 5 minutes of it.
    interval_s = 5 * 60

    def __init__(self, session: requests.Session, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask the EA and postcodes.io through `session`; `now` stamps fetched_at (a parameter for the tests)."""
        self.session = session
        self.now = now
        # Postcode -> (latitude, longitude). A postcode's place doesn't change, so this only grows.
        self.coordinates: dict[str, tuple[float, float]] = {}
        # Postcode -> (when they were asked for, the ids of the flood areas within RADIUS_KM of it, or None if
        # postcodes.io doesn't know the postcode).
        self.areas: dict[str, tuple[datetime, frozenset[str] | None]] = {}
        # When the warnings in force were last asked for, and what they were (None if the EA couldn't be asked).
        self.in_force: tuple[datetime, FloodWarningsResponse | None] | None = None

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:floods:<place>`."""
        return keys.floods(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredFloods]:
        """Fetch the warnings near each UK postcode, leaving out (and logging) any that fail or that can't be placed."""
        # One thread for the whole batch, one postcode after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredFloods]:
        # Anything else (a Norwegian postcode) is another provider's.
        postcodes = [postcode for postcode in ids if FULL_POSTCODE.fullmatch(postcode)]
        if not postcodes:
            return {}
        fetched_at, in_force = self._in_force()
        if in_force is None:
            return {}
        results: dict[str, StoredFloods] = {}
        for postcode in postcodes:
            try:
                areas = self._areas_near(postcode)
            except Exception as e:  # noqa: BLE001 -- one bad postcode mustn't lose the rest of the batch
                # Not the error's own text: an HTTP error's quotes the URL, postcode and all.
                logger.warning("Couldn't place a postcode in %s: %s", district(postcode), type(e).__name__)
                continue
            if areas is None:
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
            except Exception as e:  # noqa: BLE001 -- not raised: the runner's log of a failed batch names a postcode
                logger.warning("Couldn't fetch the flood warnings in force: %s", e)
                answer = None
            self.in_force = (now, answer)
        return self.in_force

    def _areas_near(self, postcode: str) -> frozenset[str] | None:
        """Return the ids of the flood areas near `postcode`, or None if postcodes.io doesn't know it.

        Asked for once a day; an unknown postcode is looked up again as often, as postcodes.io learns new ones.
        """
        now = self.now()
        cached = self.areas.get(postcode)
        if cached is not None and now - cached[0] < FLOOD_AREAS_REFRESH:
            return cached[1]
        coordinates = self._coordinates(postcode)
        if coordinates is None:
            logger.warning("postcodes.io doesn't know a postcode in %s; no floods for it", district(postcode))
            areas = None
        else:
            areas = fetch_flood_areas(self.session, *coordinates)
        self.areas[postcode] = (now, areas)
        return areas

    def _coordinates(self, postcode: str) -> tuple[float, float] | None:
        if postcode not in self.coordinates:
            place = lookup_postcode(self.session, postcode)
            if place is None:
                return None
            self.coordinates[postcode] = (place.latitude, place.longitude)
        return self.coordinates[postcode]


def main() -> None:
    """Run the `countdown-provider-floods` console script."""
    configure_logging()
    # The renderer's own retrying session: retries connection errors and 502/503/504 with backoff.
    session = AbstractClient._build_retrying_session()  # noqa: SLF001 -- shared with the renderer's clients on purpose
    asyncio.run(serve(FloodsProvider(session), Settings.from_env()))


if __name__ == "__main__":
    main()
