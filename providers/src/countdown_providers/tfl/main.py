"""The TfL provider: `v1:sub:tfl`'s NaPTAN stop ids to `v1:departures:<stop id>`.

It wraps countdown_core's TflSource, which makes the requests. TflSource is synchronous (requests), so each batch runs
in a worker thread (asyncio.to_thread).

A failed request is logged by its reason (log_format.error_reason: its type and status), never its text: that quotes
the URL, and TfL's app key is in it (`app_key=`).
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from countdown_core.core.http_session import retrying_session
from countdown_core.core.log_format import configure_logging, error_reason
from countdown_core.store import keys
from countdown_core.store.models import Departure, StopDepartures
from countdown_core.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_core.tfl.tfl_client import TflSource
from countdown_providers.config import Settings
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

type TflStop = SingleStopPoint | MetroStopPoint


def to_stop_departures(stop: TflStop, arrivals: list[BusArrival | TubeArrival], fetched_at: datetime) -> StopDepartures:
    """Turn TfL's stop and arrivals into the stored model, soonest first."""
    departures = [
        Departure(
            line=arrival.line,
            destination=arrival.towards if isinstance(arrival, TubeArrival) else arrival.destination,
            expected_at=fetched_at + timedelta(seconds=arrival.time_to_station),
        )
        for arrival in sorted(arrivals, key=lambda a: a.time_to_station)
        # A train ending its journey here isn't a departure, so the station's panel never shows it.
        if not (isinstance(arrival, TubeArrival) and arrival.destination_naptan_id == arrival.naptan_id)
    ]
    return StopDepartures(
        fetched_at=fetched_at,
        stop_id=stop.naptan_id,
        name=stop.common_name,
        mode="tube" if isinstance(stop, MetroStopPoint) else "bus",
        stop_letter=stop.stop_letter if isinstance(stop, SingleStopPoint) else None,
        departures=departures,
    )


class TflProvider:
    """Departures for TfL stops. Each stop is looked up once (its name and kind don't change), then only polled."""

    name = keys.TFL
    ttl_s = 120
    interval_s = 30

    def __init__(self, source: TflSource, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask TfL through `source`; `now` stamps fetched_at (a parameter for the tests)."""
        self.source = source
        self.now = now
        # NaPTAN id -> the stop TfL resolved it to. Only grows; a stop is a few hundred bytes.
        self.stops: dict[str, TflStop] = {}

    def output_key(self, item_id: str) -> str:
        """Write to the shared departures namespace."""
        return keys.departures(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StopDepartures]:
        """Fetch the stops' departures, leaving out (and logging) any that fail or aren't stops TfL knows."""
        # One thread for the whole batch, one stop after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StopDepartures]:
        results: dict[str, StopDepartures] = {}
        for stop_id in ids:
            try:
                departures = self._fetch_stop(stop_id)
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't fetch TfL stop %s: %s", stop_id, error_reason(e))
                continue
            if departures is not None:
                results[stop_id] = departures
        return results

    def _fetch_stop(self, stop_id: str) -> StopDepartures | None:
        stop = self.stops.get(stop_id)
        if stop is None:
            stop = self.source.resolve(stop_id)
            if stop is None:
                logger.warning("TfL stop %s isn't a bus stop or Tube station", stop_id)
                return None
            self.stops[stop_id] = stop
        fetched_at = self.now()
        return to_stop_departures(stop, self.source.arrivals(stop), fetched_at)


def main() -> None:
    """Run the `countdown-provider-tfl` console script. TFL_APP_KEY is optional (TfL rate-limits harder without)."""
    configure_logging()
    settings = Settings.from_env()
    # countdown_core's retrying session: retries connection errors and 502/503/504 with backoff.
    session = retrying_session()
    provider = TflProvider(TflSource(session, os.environ.get("TFL_APP_KEY", "")))
    asyncio.run(serve(provider, settings))


if __name__ == "__main__":
    main()
