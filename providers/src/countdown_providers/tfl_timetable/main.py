"""The TfL timetable provider: `v1:sub:tfl`'s stops to `v1:timetable:<stop id>`, their next timetabled departures.

TfL's Arrivals only predicts about half an hour ahead, so overnight, while the Tube is shut, a station has nothing to
show. This works out each stop's next departures from its lines' timetables (schedule.next_departures), and the TfL
provider adds a line's to the stop's departures, marked `scheduled`, while TfL predicts nothing for that line there: the
first trains of the morning, until TfL predicts them.

It runs in the TfL provider's process (tfl.main), beside it. A timetable changes rarely, and each is a few hundred KB,
so each line's (both directions) is fetched once a day per stop and kept here; every 15 minutes the next departures are
worked out again from it, and the lines' statuses for the next 12 hours fetched, as a line with a planned closure
(a weekend's engineering works) won't run its timetable, so none of its departures are written.

Bank holidays run Sunday's timetable: England and Wales's, from what the bank holidays provider stored
(`v1:notices:bank-holidays`); without it, every day runs its weekday's.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

from pydantic import ValidationError
from redis.exceptions import RedisError

from countdown_providers.tfl_timetable.schedule import LONDON, next_departures
from countdown_shared.core.log_format import error_reason
from countdown_shared.store import keys
from countdown_shared.store.bank_holidays import StoredBankHolidays
from countdown_shared.store.timetable import StoredTimetable
from countdown_shared.tfl.models import TimetableResponse

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from redis.asyncio import Redis

    from countdown_shared.tfl.models import MetroStopPoint, SingleStopPoint
    from countdown_shared.tfl.tfl_client import TflSource

    type TflStop = SingleStopPoint | MetroStopPoint

logger = logging.getLogger(__name__)

DIRECTIONS = ("inbound", "outbound")
# Statuses under which a line won't run its timetable. Not a part closure or part suspension: which stations those
# leave without trains isn't something the status says plainly, and the board's line status shows them anyway.
CLOSED = frozenset({"Planned Closure", "Closed", "Suspended"})
# How far ahead a closure hides a line's timetable: overnight, to the morning's first trains.
CLOSURE_AHEAD = timedelta(hours=12)
DIVISION = "england-and-wales"


class BankHolidayReader:
    """Reads England and Wales's bank holidays from `redis` (decode_responses=True), as their provider wrote them."""

    def __init__(self, redis: Redis) -> None:
        """Read through `redis`: the process's own client."""
        self.redis = redis

    async def holidays(self) -> dict[date, str]:
        """Return each bank holiday's date and title; none if the store has none (each day runs its weekday's)."""
        try:
            value = await self.redis.get(keys.BANK_HOLIDAYS)
        except RedisError as e:
            logger.warning("Couldn't read the bank holidays, so none this cycle: %s", error_reason(e))
            return {}
        if value is None:
            return {}
        try:
            stored = StoredBankHolidays.model_validate_json(value)
        except ValidationError:
            logger.warning("The stored bank holidays aren't StoredBankHolidays, so none this cycle")
            return {}
        division = stored.divisions.get(DIVISION)
        return {event.date: event.title for event in division.events} if division else {}


class TflTimetableProvider:
    """Each TfL stop's next timetabled departures, from its lines' timetables, fetched once a day."""

    name = keys.TFL
    ttl_s = keys.TIMETABLE_TTL_S
    interval_s = 15 * 60

    def __init__(
        self,
        source: TflSource,
        holidays: BankHolidayReader,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Ask TfL through `source`, and the store for bank holidays through `holidays`; `now` is for the tests."""
        self.source = source
        self.holiday_reader = holidays
        self.now = now
        # NaPTAN id -> the stop TfL resolved it to (its lines). Only grows.
        self.stops: dict[str, TflStop] = {}
        # (stop id, line id, direction) -> the London day it was fetched on, and TfL's answer.
        self.timetables: dict[tuple[str, str, str], tuple[date, TimetableResponse]] = {}

    def output_key(self, item_id: str) -> str:
        """Write to the stop's timetable key."""
        return keys.timetable(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredTimetable]:
        """Work out the stops' next timetabled departures, leaving out (and logging) any that fail."""
        holidays = await self.holiday_reader.holidays()
        return await asyncio.to_thread(self._fetch_blocking, ids, holidays)

    def _fetch_blocking(self, ids: list[str], holidays: Mapping[date, str]) -> dict[str, StoredTimetable]:
        now = self.now()
        results: dict[str, StoredTimetable] = {}
        for stop_id in ids:
            try:
                stored = self._fetch_stop(stop_id, now, holidays)
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't work out TfL stop %s's timetable: %s", stop_id, error_reason(e))
                continue
            if stored is not None:
                results[stop_id] = stored
        return results

    def _fetch_stop(self, stop_id: str, now: datetime, holidays: Mapping[date, str]) -> StoredTimetable | None:
        stop = self.stops.get(stop_id)
        if stop is None:
            stop = self.source.resolve(stop_id)
            if stop is None:
                logger.warning("TfL stop %s isn't a bus stop or Tube station", stop_id)
                return None
            self.stops[stop_id] = stop
        line_ids = [line.id for line in stop.lines if line.id not in self._closed(stop, now)]
        departures = [
            departure
            for line_id in line_ids
            for direction in DIRECTIONS
            if (response := self._timetable(stop_id, line_id, direction, now)) is not None
            for departure in next_departures(response, stop_id, now, holidays)
        ]
        departures.sort(key=lambda departure: departure.departs_at)
        return StoredTimetable(fetched_at=now, stop_id=stop_id, departures=departures)

    def _closed(self, stop: TflStop, now: datetime) -> set[str]:
        """Return which of the stop's lines are closed (CLOSED) at some point in the next CLOSURE_AHEAD."""
        if not stop.lines:
            return set()
        start = now.astimezone(LONDON).replace(tzinfo=None)
        try:
            lines = self.source.line_status_between([line.id for line in stop.lines], start, start + CLOSURE_AHEAD)
        except Exception as e:  # noqa: BLE001 -- without the statuses, every line's timetable is shown
            logger.warning("Couldn't fetch TfL stop %s's lines' statuses: %s", stop.naptan_id, error_reason(e))
            return set()
        return {line.id for line in lines if any(status.description in CLOSED for status in line.line_statuses)}

    def _timetable(self, stop_id: str, line_id: str, direction: str, now: datetime) -> TimetableResponse | None:
        """Return the line's timetable from the stop going `direction`: today's copy, else fetched again.

        A failed fetch keeps the copy from before, if any: a timetable rarely changes from one day to the next.
        """
        today = now.astimezone(LONDON).date()
        key = (stop_id, line_id, direction)
        cached = self.timetables.get(key)
        if cached is not None and cached[0] == today:
            return cached[1]
        try:
            response = self.source.timetable(line_id, stop_id, direction)
        except Exception as e:  # noqa: BLE001 -- one line's timetable mustn't lose the stop's others
            if getattr(getattr(e, "response", None), "status_code", None) != HTTPStatus.NOT_FOUND:
                logger.warning(
                    "Couldn't fetch the %s timetable (%s) at TfL stop %s: %s",
                    line_id,
                    direction,
                    stop_id,
                    error_reason(e),
                )
                return cached[1] if cached is not None else None
            # TfL's answer for a direction the line doesn't run from the stop (a bus stop's other side): no timetable.
            response = TimetableResponse()
        self.timetables[key] = (today, response)
        return response
