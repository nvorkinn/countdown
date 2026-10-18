"""The TfL notices provider: TfL's disruptions for the notice boards, written to the `v1:notices:tfl-*` keys.

Three providers in one process (runner.serve_all), one for each kind of TfL disruption the notice board shows:

- Line status, for the stops in `v1:sub:tfl`: each stop's lines are looked up once a day (TfL's /StopPoint/{id}),
  and every two minutes TfL is asked about every line any stop needs at once (/Line/{ids}/Status, a request per
  LINES_PER_REQUEST lines); each stop gets its own lines' statuses, at `v1:notices:tfl-lines:<stop id>`.
- Stations, for the same stops: /StopPoint/{id}/Disruption for each, every five minutes, at
  `v1:notices:tfl-stations:<stop id>`.
- Roads, for the UK postcodes in `v1:sub:postcode` (a Norwegian one, four digits, has no TfL roads): one
  /Road/all/Disruption every five minutes, cut to what's within ROAD_RADIUS_KM of each postcode (placed by
  postcodes.io, once), at `v1:notices:tfl-roads:<place>`.

What's stored is TfL's own answers (store/tfl_notices.py); the renderer makes the notices from them as it draws. The
requests are countdown-core's (TflSource, lookup_postcode), which are synchronous (requests), so each batch runs in a
worker thread (asyncio.to_thread), with a session of its own.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import TYPE_CHECKING

import requests

from countdown_core.core.abstract_client import AbstractClient
from countdown_core.core.log_format import configure_logging
from countdown_core.notices.location import FULL_POSTCODE, lookup_postcode
from countdown_core.notices.tfl import ROAD_SEVERITY, is_near
from countdown_core.store import keys
from countdown_core.store.tfl_notices import StoredLineStatus, StoredRoadDisruptions, StoredStationDisruptions
from countdown_core.tfl.tfl_client import TflSource
from countdown_providers.config import Settings
from countdown_providers.runner import serve_all

if TYPE_CHECKING:
    from collections.abc import Callable

    from countdown_core.tfl.models import Line, Postcode

logger = logging.getLogger(__name__)

# More ids than any deploy subscribes: a provider with this batch_size gets every id in its shard in one batch, so it
# asks TfL once a cycle for what they share.
EVERY_ID = 10_000
# Lines asked about in one /Line/{ids}/Status, so the URL stays a sensible length however many stops there are.
LINES_PER_REQUEST = 50
# How long a stop's lines are taken as they were: bus routes change now and then, not from minute to minute.
LINES_LOOKUP_EVERY = timedelta(days=1)


def _reason(e: Exception) -> str:
    """Say what went wrong, without a failed request's URL: it has the TfL app key in it, or a postcode."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"{type(e).__name__} {e.response.status_code}"
    return type(e).__name__


class TflLineStatusProvider:
    """The status of the lines serving each TfL stop, from one request for every line the stops share."""

    name = keys.TFL
    ttl_s = 10 * 60
    interval_s = 2 * 60
    batch_size = EVERY_ID

    def __init__(self, tfl: TflSource, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask TfL through `tfl`; `now` stamps fetched_at and dates the stops' lines (a parameter for the tests)."""
        self.tfl = tfl
        self.now = now
        # NaPTAN id -> when its lines were looked up, and their ids in TfL's order.
        self.stop_lines: dict[str, tuple[datetime, list[str]]] = {}

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:tfl-lines:<stop id>`."""
        return keys.tfl_line_status(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredLineStatus]:
        """Fetch the stops' lines' statuses, leaving out (and logging) a stop whose lines couldn't be asked about."""
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredLineStatus]:
        stop_lines: dict[str, list[str]] = {}
        for stop_id in ids:
            try:
                stop_lines[stop_id] = self._lines_at(stop_id)
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't look up the lines at TfL stop %s: %s", stop_id, _reason(e))
        # Each line once, however many stops it serves.
        wanted = list(dict.fromkeys(line_id for line_ids in stop_lines.values() for line_id in line_ids))
        fetched_at = self.now()
        statuses: dict[str, Line] = {}
        failed: set[str] = set()
        for chunk in batched(wanted, LINES_PER_REQUEST, strict=False):
            try:
                statuses |= {line.id: line for line in self.tfl.line_status(chunk)}
            except Exception as e:  # noqa: BLE001 -- only the stops these lines serve go without
                logger.warning(
                    "Couldn't fetch the status of %d TfL lines (%s...): %s", len(chunk), chunk[0], _reason(e)
                )
                failed.update(chunk)
        return {
            # A line TfL leaves out of its answer has nothing to report.
            stop_id: StoredLineStatus(
                fetched_at=fetched_at, stop_id=stop_id, lines=[statuses[i] for i in line_ids if i in statuses]
            )
            for stop_id, line_ids in stop_lines.items()
            # Its key keeps the last statuses until they expire.
            if not failed.intersection(line_ids)
        }

    def _lines_at(self, stop_id: str) -> list[str]:
        """Return the ids of the lines serving the stop, asking TfL once a day; raises if TfL never could be asked."""
        cached = self.stop_lines.get(stop_id)
        now = self.now()
        if cached is not None and now - cached[0] < LINES_LOOKUP_EVERY:
            return cached[1]
        try:
            stop = self.tfl.resolve(stop_id)
        except Exception:
            if cached is None:
                raise
            return cached[1]  # yesterday's lines, until TfL answers again
        if stop is None:
            logger.warning("TfL stop %s isn't a bus stop or Tube station: no lines to watch", stop_id)
        line_ids = [] if stop is None else list(dict.fromkeys(line.id for line in stop.lines))
        self.stop_lines[stop_id] = (now, line_ids)
        return line_ids


class TflStationProvider:
    """What's wrong at each TfL stop (lifts out of order, closed entrances...), a request per stop."""

    name = keys.TFL
    ttl_s = 30 * 60
    interval_s = 5 * 60

    def __init__(self, tfl: TflSource, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask TfL through `tfl`; `now` stamps fetched_at (a parameter for the tests)."""
        self.tfl = tfl
        self.now = now

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:tfl-stations:<stop id>`."""
        return keys.tfl_stations(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredStationDisruptions]:
        """Fetch each stop's disruptions, leaving out (and logging) any that fail."""
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredStationDisruptions]:
        results: dict[str, StoredStationDisruptions] = {}
        for stop_id in ids:
            fetched_at = self.now()
            try:
                disruptions = self.tfl.stop_disruptions(stop_id)
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't fetch the disruptions at TfL stop %s: %s", stop_id, _reason(e))
                continue
            results[stop_id] = StoredStationDisruptions(fetched_at=fetched_at, stop_id=stop_id, disruptions=disruptions)
        return results


class TflRoadProvider:
    """The serious and severe road disruptions near each UK postcode, from one request for all of London's."""

    name = keys.POSTCODE
    ttl_s = 30 * 60
    interval_s = 5 * 60
    batch_size = EVERY_ID

    def __init__(
        self, tfl: TflSource, session: requests.Session, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        """Ask TfL through `tfl` and postcodes.io over `session`; `now` stamps fetched_at (a parameter for tests)."""
        self.tfl = tfl
        self.session = session
        self.now = now
        # Postcode -> where postcodes.io placed it. A postcode's place doesn't change, so this only grows (a few dozen
        # bytes each); one postcodes.io doesn't know is asked about again next cycle.
        self.places: dict[str, Postcode] = {}

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:tfl-roads:<place>`."""
        return keys.tfl_roads(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredRoadDisruptions]:
        """Fetch the disruptions near each UK postcode, leaving out (and logging) any that can't be placed."""
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredRoadDisruptions]:
        # Postcodes stay out of the logs: they say where someone lives.
        places: dict[str, Postcode] = {}
        for postcode in ids:
            if not FULL_POSTCODE.fullmatch(postcode):
                continue  # a Norwegian one: TfL has nothing to say about Norway's roads
            try:
                place = self._place(postcode)
            except Exception as e:  # noqa: BLE001 -- one bad postcode mustn't lose the rest of the batch
                logger.warning("Couldn't look up a postcode: %s", _reason(e))
                continue
            if place is None:
                logger.warning("A subscribed postcode isn't one postcodes.io knows; no road notices for it")
                continue
            places[postcode] = place
        if not places:
            return {}
        fetched_at = self.now()
        try:
            disruptions = self.tfl.road_disruptions(ROAD_SEVERITY)
        except Exception as e:  # noqa: BLE001 -- not raised, as the runner would log the batch's first postcode
            logger.warning("Couldn't fetch TfL's road disruptions: %s", _reason(e))
            return {}
        return {
            postcode: StoredRoadDisruptions(
                fetched_at=fetched_at,
                postcode=postcode,
                disruptions=[d for d in disruptions if is_near(d, place.latitude, place.longitude)],
            )
            for postcode, place in places.items()
        }

    def _place(self, postcode: str) -> Postcode | None:
        if postcode not in self.places:
            place = lookup_postcode(self.session, postcode)
            if place is None:
                return None
            self.places[postcode] = place
        return self.places[postcode]


def providers(app_key: str = "") -> list[TflLineStatusProvider | TflStationProvider | TflRoadProvider]:
    """Make the three providers, each with a session of its own (they run in threads side by side)."""
    # The renderer's own retrying session: retries connection errors and 502/503/504 with backoff.
    session = AbstractClient._build_retrying_session  # noqa: SLF001 -- shared with the renderer's clients on purpose
    roads = session()
    return [
        TflLineStatusProvider(TflSource(session(), app_key)),
        TflStationProvider(TflSource(session(), app_key)),
        TflRoadProvider(TflSource(roads, app_key), roads),
    ]


def main() -> None:
    """Run the `countdown-provider-tfl-notices` console script. TFL_APP_KEY is optional (TfL rate-limits harder)."""
    configure_logging()
    settings = Settings.from_env()
    asyncio.run(serve_all(providers(os.environ.get("TFL_APP_KEY", "")), settings))


if __name__ == "__main__":
    main()
