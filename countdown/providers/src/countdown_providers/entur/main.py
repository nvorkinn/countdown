"""The Entur provider: `v1:sub:entur`'s NSR stop ids (stop places and quays) to `v1:departures:<stop id>`.

It wraps countdown_core's EnturSource, the same GraphQL queries (and ET-Client-Name header) the renderer's departures
client sends. EnturSource is synchronous (requests), so each batch runs in a worker thread (asyncio.to_thread), like
the TfL provider. Entur needs no key; identified clients may make 1000 non-trip requests a minute
(https://developer.entur.no/docs/open-services/journey-planner/rate-limiting), and a stop costs two a minute here.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import AbstractClient
from countdown_core.core.log_format import configure_logging
from countdown_core.entur.entur_client import TransportMode
from countdown_core.entur.entur_source import EnturSource
from countdown_core.store import keys
from countdown_core.store.models import Departure, StopDepartures
from countdown_providers.config import Settings
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

    from countdown_core.entur.entur_client import EnturDeparture, EnturStop

logger = logging.getLogger(__name__)


def to_stop_departures(stop: EnturStop, departures: list[EnturDeparture], fetched_at: datetime) -> StopDepartures:
    """Turn Entur's stop and departures into the stored model, soonest first.

    Keeps everything EnturArrivalPanel draws from: the stop's modes (the header's icons), each departure's mode (the
    rows' icons) and a quay's own code (where a London stop has its letter; None for a whole stop place). Each
    departure's platform is the quay it leaves from, which the panel doesn't draw.
    """
    return StopDepartures(
        fetched_at=fetched_at,
        stop_id=stop.id,
        name=stop.name,
        mode=(stop.transport_modes[0] if stop.transport_modes else TransportMode.UNKNOWN).value,
        stop_letter=stop.public_code,
        modes=[mode.value for mode in stop.transport_modes],
        departures=[
            Departure(
                line=departure.line,
                destination=departure.destination,
                expected_at=departure.expected_departure_time,
                platform=departure.quay_code,
                mode=departure.mode.value,
            )
            for departure in sorted(departures, key=lambda d: d.time_to_station)
        ],
    )


class EnturProvider:
    """Departures for Entur stops. Each stop is looked up once (its name and modes don't change), then only polled."""

    name = keys.ENTUR
    ttl_s = 120
    # As the TfL provider: well inside Entur's limits (see above), and as fresh as the renderers' minute-long polls.
    interval_s = 30

    def __init__(self, source: EnturSource, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask Entur through `source`; `now` stamps fetched_at and is when departures are asked from (for the tests)."""
        self.source = source
        self.now = now
        # NSR id -> the stop Entur resolved it to. Only grows; a stop is a few hundred bytes.
        self.stops: dict[str, EnturStop] = {}

    def output_key(self, item_id: str) -> str:
        """Write to the shared departures namespace."""
        return keys.departures(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StopDepartures]:
        """Fetch the stops' departures, leaving out (and logging) any that fail or that Entur doesn't know."""
        # One thread for the whole batch, one stop after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StopDepartures]:
        results: dict[str, StopDepartures] = {}
        for stop_id in ids:
            try:
                departures = self._fetch_stop(stop_id)
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't fetch Entur stop %s: %s", stop_id, e)
                continue
            if departures is not None:
                results[stop_id] = departures
        return results

    def _fetch_stop(self, stop_id: str) -> StopDepartures | None:
        stop = self.stops.get(stop_id)
        if stop is None:
            # Not cached when Entur doesn't know it, so a stop it learns about later is picked up.
            stop = self.source.resolve(stop_id)
            if stop is None:
                logger.warning("Entur doesn't know stop %s", stop_id)
                return None
            self.stops[stop_id] = stop
        fetched_at = self.now()
        return to_stop_departures(stop, self.source.departures(stop, now=fetched_at), fetched_at)


def main() -> None:
    """Run the `countdown-provider-entur` console script. Entur needs no key, only the client's ET-Client-Name."""
    configure_logging()
    settings = Settings.from_env()
    # The renderer's own retrying session: retries connection errors and 502/503/504 with backoff.
    session = AbstractClient._build_retrying_session()  # noqa: SLF001 -- shared with the renderer's clients on purpose
    asyncio.run(serve(EnturProvider(EnturSource(session)), settings))


if __name__ == "__main__":
    main()
