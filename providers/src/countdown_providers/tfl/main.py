"""The TfL provider: `v1:sub:tfl`'s NaPTAN stop ids to `v1:departures:<stop id>`.

It wraps countdown_shared's TflSource, which makes the requests. TflSource is synchronous (requests), so each batch runs
in a worker thread (asyncio.to_thread).

TfL's Arrivals only predicts about half an hour ahead, so while the Tube is shut a station has none. The TfL timetable
provider (tfl_timetable), which runs in this process beside it, stores each stop's next timetabled departures
(`v1:timetable:<stop id>`); for a line TfL predicts nothing for at the stop, its timetabled departures are added,
marked `scheduled`, until TfL predicts it again.

A failed request is logged by its reason (log_format.error_reason: its type and status), never its text: that quotes
the URL, and TfL's app key is in it (`app_key=`).
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError
from redis.exceptions import RedisError

from countdown_providers.config import Settings
from countdown_providers.runner import serve_all
from countdown_providers.tfl_timetable.main import BankHolidayReader, TflTimetableProvider
from countdown_shared.core.http_session import retrying_session
from countdown_shared.core.log_format import configure_logging, error_reason
from countdown_shared.store import keys
from countdown_shared.store.models import Departure, StopDepartures
from countdown_shared.store.timetable import StoredTimetable
from countdown_shared.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_shared.tfl.tfl_client import TflSource

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

type TflStop = SingleStopPoint | MetroStopPoint


def to_stop_departures(
    stop: TflStop,
    arrivals: list[BusArrival | TubeArrival],
    fetched_at: datetime,
    timetable: StoredTimetable | None = None,
) -> StopDepartures:
    """Turn TfL's stop and arrivals into the stored model, soonest first.

    With the stop's `timetable`, each line TfL predicts nothing for gets its timetabled departures from then on, marked
    `scheduled`.
    """
    departures = [
        Departure(
            line=arrival.line,
            destination=arrival.towards if isinstance(arrival, TubeArrival) else arrival.destination,
            expected_at=fetched_at + timedelta(seconds=arrival.time_to_station),
            platform=arrival.platform_name if isinstance(arrival, TubeArrival) else None,
            vehicle_id=arrival.vehicle_id or None,
        )
        for arrival in sorted(arrivals, key=lambda a: a.time_to_station)
        # A train ending its journey here isn't a departure, so the station's panel never shows it.
        if not (isinstance(arrival, TubeArrival) and arrival.destination_naptan_id == arrival.naptan_id)
    ]
    if timetable is not None:
        predicted = {arrival.line.casefold() for arrival in arrivals}
        departures += [
            Departure(line=timed.line, destination=timed.destination, expected_at=timed.departs_at, scheduled=True)
            for timed in timetable.departures
            if timed.line.casefold() not in predicted and timed.departs_at >= fetched_at
        ]
        departures.sort(key=lambda departure: departure.expected_at)
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
    ttl_s = keys.DEPARTURES_TTL_S
    interval_s = 30

    def __init__(
        self,
        source: TflSource,
        timetables: TimetableReader | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Ask TfL through `source`, and the store for the stops' timetables through `timetables`.

        Without `timetables`, the departures are TfL's predictions alone. `now` stamps fetched_at (for the tests).
        """
        self.source = source
        self.timetables = timetables
        self.now = now
        # NaPTAN id -> the stop TfL resolved it to. Only grows; a stop is a few hundred bytes.
        self.stops: dict[str, TflStop] = {}

    def output_key(self, item_id: str) -> str:
        """Write to the shared departures namespace."""
        return keys.departures(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StopDepartures]:
        """Fetch the stops' departures, leaving out (and logging) any that fail or aren't stops TfL knows."""
        timetables = await self.timetables.read(ids) if self.timetables is not None else {}
        # One thread for the whole batch, one stop after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, ids, timetables)

    def _fetch_blocking(self, ids: list[str], timetables: dict[str, StoredTimetable]) -> dict[str, StopDepartures]:
        results: dict[str, StopDepartures] = {}
        for stop_id in ids:
            try:
                departures = self._fetch_stop(stop_id, timetables.get(stop_id))
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't fetch TfL stop %s: %s", stop_id, error_reason(e))
                continue
            if departures is not None:
                results[stop_id] = departures
        return results

    def _fetch_stop(self, stop_id: str, timetable: StoredTimetable | None) -> StopDepartures | None:
        stop = self.stops.get(stop_id)
        if stop is None:
            stop = self.source.resolve(stop_id)
            if stop is None:
                logger.warning("TfL stop %s isn't a bus stop or Tube station", stop_id)
                return None
            self.stops[stop_id] = stop
        fetched_at = self.now()
        return to_stop_departures(stop, self.source.arrivals(stop), fetched_at, timetable)


class TimetableReader:
    """Reads the stops' timetabled departures from `redis` (decode_responses=True), as the timetable provider wrote.

    A stop with none stored (the timetable provider hasn't got to it, or is down), or one that can't be read, just has
    no timetable: its departures are TfL's predictions alone.
    """

    def __init__(self, redis: Redis) -> None:
        """Read through `redis`: the process's own client, which its runners use too."""
        self.redis = redis

    async def read(self, stop_ids: Sequence[str]) -> dict[str, StoredTimetable]:
        """Return each stop's stored timetable, leaving out those with none. Never raises."""
        if not stop_ids:
            return {}  # MGET needs a key
        try:
            values = await self.redis.mget([keys.timetable(stop_id) for stop_id in stop_ids])
        except RedisError as e:
            logger.warning("Couldn't read the TfL stops' timetables, so live departures only: %s", error_reason(e))
            return {}
        timetables: dict[str, StoredTimetable] = {}
        for stop_id, value in zip(stop_ids, values, strict=True):
            if value is None:
                continue
            try:
                timetables[stop_id] = StoredTimetable.model_validate_json(value)
            except ValidationError:
                logger.warning("TfL stop %s's stored timetable isn't a StoredTimetable", stop_id)
        return timetables


def main() -> None:
    """Run the `countdown-provider-tfl` console script: the departures, and the timetables beside them.

    TFL_APP_KEY is optional (TfL rate-limits harder without).
    """
    configure_logging()
    settings = Settings.from_env()
    app_key = os.environ.get("TFL_APP_KEY", "")
    redis = settings.redis()
    # countdown_shared's retrying session: retries connection errors and 502/503/504 with backoff. One each, as each
    # provider's batches run in their own threads, and a requests session isn't shared across threads.
    providers = [
        TflProvider(TflSource(retrying_session(), app_key), TimetableReader(redis)),
        TflTimetableProvider(TflSource(retrying_session(), app_key), BankHolidayReader(redis)),
    ]
    asyncio.run(serve_all(providers, settings, redis=redis))


if __name__ == "__main__":
    main()
