"""The TfL notices provider: TfL's disruptions for the notice boards, written to the `v1:notices:tfl-*` keys.

Four providers in one process (runner.serve_all), one for each kind of TfL disruption the notice board shows, and one
for the network as a whole:

- Line status, for the stops in `v1:sub:tfl`: each stop's lines are looked up once a day (TfL's /StopPoint/{id}),
  and every two minutes TfL is asked about every line any stop needs at once (/Line/{ids}/Status, a request per
  LINES_PER_REQUEST lines); each stop gets its own lines' statuses, at `v1:notices:tfl-lines:<stop id>`.
- Stations, for the same stops: /StopPoint/{id}/Disruption for each, every five minutes, at
  `v1:notices:tfl-stations:<stop id>`.
- Roads, for the UK postcodes in `v1:sub:postcode` (a Norwegian one, four digits, has no TfL roads): one
  /Road/all/Disruption every five minutes, cut to what's within ROAD_RADIUS_KM of each postcode, at
  `v1:notices:tfl-roads:<place>`. Where a postcode is comes from the store, as provider-locations placed it
  (`v1:location:<place>`, read each cycle by locations.reader.LocationReader, on the process's one Redis client); one
  it hasn't placed, or that doesn't exist, is left out until it has.
- The network, with no subscription (fixed_ids, like the bank holidays): every Tube line and the Elizabeth line
  (/Line/Mode/tube,elizabeth-line/Status) every two minutes, at `v1:notices:tfl-network`, for a touchscreen's Tube
  status panel, which shows them all when its notice board has nothing to say.

What's stored is TfL's own answers (store/tfl_notices.py); whatever shows a notice makes it from them as it draws.
A line status's reason is TfL's prose, too long for a touchscreen's row, so those worth a notice are stored with a
model's short form beside them (`shorts`; shortening.Shortener asks once per text and keeps the answer). The
requests are countdown-shared's (TflSource), which are synchronous (requests), so each batch runs in a worker thread
(asyncio.to_thread), with a session of its own.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import TYPE_CHECKING

from countdown_providers.config import Settings
from countdown_providers.locations.reader import LocationReader
from countdown_providers.runner import serve_all
from countdown_providers.shortening import Shortener
from countdown_shared.core.http_session import retrying_session
from countdown_shared.core.log_format import configure_logging, error_reason
from countdown_shared.notices.location import FULL_POSTCODE
from countdown_shared.notices.shortening import SUMMARY_MAX, squash
from countdown_shared.notices.tfl_feed import LINE_STATUSES, ROAD_SEVERITIES, is_near
from countdown_shared.openai import text as text_model
from countdown_shared.store import keys
from countdown_shared.store.tfl_notices import (
    NoticeShort,
    StoredLineStatus,
    StoredNetworkStatus,
    StoredRoadDisruptions,
    StoredStationDisruptions,
)
from countdown_shared.tfl.tfl_client import TflSource

if TYPE_CHECKING:
    from collections.abc import Callable

    from countdown_shared.tfl.models import Line, Postcode

logger = logging.getLogger(__name__)

# More ids than any deploy subscribes: a provider with this batch_size gets every id in its shard in one batch, so it
# asks TfL once a cycle for what they share.
EVERY_ID = 10_000
# Lines asked about in one /Line/{ids}/Status, so the URL stays a sensible length however many stops there are.
LINES_PER_REQUEST = 50
# How long a stop's lines are taken as they were: bus routes change now and then, not from minute to minute.
LINES_LOOKUP_EVERY = timedelta(days=1)
# The modes the network's status covers: TfL's names, as /Line/Mode/{modes}/Status takes them.
NETWORK_MODES = ("tube", "elizabeth-line")


class TflLineStatusProvider:
    """The status of the lines serving each TfL stop, from one request for every line the stops share."""

    name = keys.TFL
    ttl_s = keys.TFL_LINE_STATUS_TTL_S
    interval_s = 2 * 60
    batch_size = EVERY_ID

    def __init__(
        self,
        tfl: TflSource,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        shortener: Shortener | None = None,
    ) -> None:
        """Ask TfL through `tfl`; `now` stamps fetched_at and dates the stops' lines (a parameter for the tests).

        With a `shortener`, the reasons too long for a touchscreen's row are stored with a model's short form beside
        them (`shorts`); without one, as TfL wrote them alone.
        """
        self.tfl = tfl
        self.now = now
        self.shortener = shortener
        # NaPTAN id -> when its lines were looked up, and their ids in TfL's order.
        self.stop_lines: dict[str, tuple[datetime, list[str]]] = {}

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:tfl-lines:<stop id>`."""
        return keys.tfl_line_status(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredLineStatus]:
        """Fetch the stops' lines' statuses, leaving out (and logging) a stop whose lines couldn't be asked about."""
        fetched = await asyncio.to_thread(self._fetch_blocking, ids)
        await self._shorten(fetched)
        return fetched

    async def _shorten(self, fetched: dict[str, StoredLineStatus]) -> None:
        """Put beside each stop's lines the short forms of their reasons, of those that have one (see _long_reasons)."""
        if self.shortener is None:
            return
        reasons = {stop_id: _long_reasons(stored.lines, self.now()) for stop_id, stored in fetched.items()}
        # Each reason once, however many stops' lines have it; in a stable order, so the same few are asked about
        # first whichever stop comes first.
        shorts = await self.shortener.shorts("line", sorted({reason for found in reasons.values() for reason in found}))
        for stop_id, stored in fetched.items():
            stored.shorts = [
                NoticeShort(text=reason, title=shorts[reason].title, summary=shorts[reason].summary)
                for reason in reasons[stop_id]
                if reason in shorts
            ]

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredLineStatus]:
        stop_lines: dict[str, list[str]] = {}
        for stop_id in ids:
            try:
                stop_lines[stop_id] = self._lines_at(stop_id)
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't look up the lines at TfL stop %s: %s", stop_id, error_reason(e))
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
                    "Couldn't fetch the status of %d TfL lines (%s...): %s", len(chunk), chunk[0], error_reason(e)
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


def _long_reasons(lines: list[Line], now: datetime) -> list[str]:
    """Return the reasons worth shortening among the lines' statuses, squashed, each once, in the lines' order.

    Those of the statuses worth a notice (tfl_feed.LINE_STATUSES) that haven't ended, and are too long to be the line
    under a notice's title as they are.
    """
    reasons: dict[str, None] = {}
    for line in lines:
        for status in line.line_statuses:
            reason = squash(status.reason or "")
            over = status.validity_periods and all(period.to_date < now for period in status.validity_periods)
            if status.description in LINE_STATUSES and len(reason) > SUMMARY_MAX and not over:
                reasons[reason] = None
    return list(reasons)


class TflStationProvider:
    """What's wrong at each TfL stop (lifts out of order, closed entrances...), a request per stop."""

    name = keys.TFL
    ttl_s = keys.TFL_STATIONS_TTL_S
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
                logger.warning("Couldn't fetch the disruptions at TfL stop %s: %s", stop_id, error_reason(e))
                continue
            results[stop_id] = StoredStationDisruptions(fetched_at=fetched_at, stop_id=stop_id, disruptions=disruptions)
        return results


class TflRoadProvider:
    """The serious and severe road disruptions near each UK postcode, from one request for all of London's."""

    name = keys.POSTCODE
    ttl_s = keys.TFL_ROADS_TTL_S
    interval_s = 5 * 60
    batch_size = EVERY_ID
    # A cycle as soon as provider-locations places a new postcode (or moves one), not at the next tick.
    wakes_on = (keys.LOCATION_CHANGED,)

    def __init__(
        self, tfl: TflSource, locations: LocationReader, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        """Ask TfL through `tfl`, and the store where each postcode is through `locations`.

        `now` stamps fetched_at (a parameter for tests).
        """
        self.tfl = tfl
        self.locations = locations
        self.now = now

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:tfl-roads:<place>`."""
        return keys.tfl_roads(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredRoadDisruptions]:
        """Fetch the disruptions near each UK postcode, leaving out (and logging) any that can't be placed."""
        # A Norwegian postcode is left out: TfL has nothing to say about Norway's roads.
        postcodes = [postcode for postcode in ids if FULL_POSTCODE.fullmatch(postcode)]
        places = await self.locations.places(postcodes, notices="TfL road notices")
        if not places:
            return {}
        return await asyncio.to_thread(self._fetch_blocking, places)

    def _fetch_blocking(self, places: dict[str, Postcode]) -> dict[str, StoredRoadDisruptions]:
        fetched_at = self.now()
        try:
            disruptions = self.tfl.road_disruptions(ROAD_SEVERITIES)
        except Exception as e:  # noqa: BLE001 -- the places keep their last keys until they expire
            logger.warning("Couldn't fetch TfL's road disruptions: %s", error_reason(e))
            return {}
        return {
            postcode: StoredRoadDisruptions(
                fetched_at=fetched_at,
                postcode=postcode,
                disruptions=[d for d in disruptions if is_near(d, place.latitude, place.longitude)],
            )
            for postcode, place in places.items()
        }


class TflNetworkStatusProvider:
    """Every Tube line's status and the Elizabeth line's, for every London touchscreen, from one request."""

    # Not a subscription source (it has fixed_ids): only its name in the logs.
    name = "tfl-network"
    # Its one id: the network, the same for every screen.
    fixed_ids = ("london",)
    ttl_s = keys.TFL_NETWORK_TTL_S
    interval_s = 2 * 60

    def __init__(self, tfl: TflSource, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask TfL through `tfl`; `now` stamps fetched_at (a parameter for the tests)."""
        self.tfl = tfl
        self.now = now

    def output_key(self, item_id: str) -> str:  # noqa: ARG002 -- one key for its one id
        """Write to `v1:notices:tfl-network`."""
        return keys.TFL_NETWORK

    async def fetch(self, ids: list[str]) -> dict[str, StoredNetworkStatus]:
        """Fetch the network's lines. Raises if TfL can't be asked, keeping the stored ones until they expire."""
        if self.fixed_ids[0] not in ids:
            return {}
        return {self.fixed_ids[0]: await asyncio.to_thread(self._fetch_blocking)}

    def _fetch_blocking(self) -> StoredNetworkStatus:
        fetched_at = self.now()
        return StoredNetworkStatus(fetched_at=fetched_at, lines=self.tfl.mode_status(NETWORK_MODES))


def providers(
    locations: LocationReader, app_key: str = "", shortener: Shortener | None = None
) -> list[TflLineStatusProvider | TflStationProvider | TflRoadProvider | TflNetworkStatusProvider]:
    """Make the four providers, each with a session of its own (they run in threads side by side).

    The road provider reads where the postcodes are through `locations`; the line statuses' reasons are shortened
    through `shortener`, if there is one.
    """
    # countdown_shared's retrying session: retries connection errors and 502/503/504 with backoff.
    return [
        TflLineStatusProvider(TflSource(retrying_session(), app_key), shortener=shortener),
        TflStationProvider(TflSource(retrying_session(), app_key)),
        TflRoadProvider(TflSource(retrying_session(), app_key), locations),
        TflNetworkStatusProvider(TflSource(retrying_session(), app_key)),
    ]


def main() -> None:
    """Run the `countdown-provider-tfl-notices` console script.

    TFL_APP_KEY is optional (TfL rate-limits harder), and so is OPENAI_API_KEY (the reasons go unshortened).
    """
    configure_logging()
    settings = Settings.from_env()
    # One client for the four runners, the locations the roads read and the short forms the line statuses keep.
    redis = settings.redis()
    model = text_model.configured()
    if model is None:
        logger.warning("No OPENAI_API_KEY: the line statuses' reasons are stored without short forms")
    shortener = Shortener(redis, model)
    serving = providers(LocationReader(redis), os.environ.get("TFL_APP_KEY", ""), shortener)
    asyncio.run(serve_all(serving, settings, redis=redis))


if __name__ == "__main__":
    main()
