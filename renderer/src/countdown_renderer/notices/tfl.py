"""TfL's disruptions: on the lines and at the stations the screen shows, and on the roads near it.

TfL is asked once for every screen, by the TfL notices provider (countdown_providers.tfl_notices), which stores its
answers (store/tfl_notices.py): by stop, the status of the lines serving it and what's wrong at it; by place, the road
incidents near it. These sources read them and make the notices as the board polls, so the tags are in the screen's
language and what's current is judged as of drawing. The wording itself is countdown_shared's
(notices/wording/tfl.py).
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource
from countdown_shared.notices.wording.tfl import line_status_notices, road_notices, station_notices
from countdown_shared.store import keys, reader
from countdown_shared.store.tfl_notices import StoredLineStatus, StoredRoadDisruptions, StoredStationDisruptions

if TYPE_CHECKING:
    from datetime import datetime

    import requests

    from countdown_shared.store.models import Stored
    from countdown_shared.tfl.models import Line

__all__ = [
    "NoStoredTflNoticesError",
    "StoredTflLineStatusSource",
    "StoredTflNoticeSource",
    "StoredTflRoadSource",
    "StoredTflStationSource",
    "line_status_notices",
    "road_notices",
    "station_notices",
]


class NoStoredTflNoticesError(reader.NoStoredDataError):
    """The store has nothing for a stop or place: the TfL notices provider is down, or hasn't fetched it yet."""


class StoredTflNoticeSource(NoticeSource):
    """A notice source reading what the TfL notices provider stored, rather than asking TfL."""

    def _read_all[M: Stored](self, item_keys: list[str], model: type[M]) -> list[M]:
        """Return the values stored under `item_keys`, in order, in one round trip.

        Raises NoStoredTflNoticesError if any is missing, so the board leaves the source out rather than show half of
        it, StoreNotConfiguredError without a store, and RedisError if Redis can't be asked. The store is looked up
        here rather than when the source is made, so the board can build its sources without one.
        """
        if not item_keys:
            return []
        values = reader.require_store().mget(item_keys)
        if missing := [key for key, value in zip(item_keys, values, strict=True) if value is None]:
            msg = f"Nothing in the store at {', '.join(missing)}"
            raise NoStoredTflNoticesError(msg)
        return [model.model_validate_json(value) for value in values if value is not None]


class StoredTflLineStatusSource(StoredTflNoticeSource):
    """Delays, closures and suspensions on the lines and bus routes serving the stops, as stored by stop."""

    name = "TfL line status"

    def __init__(self, session: requests.Session, stop_ids: list[str]) -> None:
        """Read the status of the lines serving the TfL stops `stop_ids`; `session` is the board's (unused)."""
        super().__init__(session)
        self.stop_ids = stop_ids

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        # Every line once, in the order the stops list them: a line serving two stops is one line.
        lines: dict[str, Line] = {}
        for stored in self._read_all([keys.tfl_line_status(stop_id) for stop_id in self.stop_ids], StoredLineStatus):
            for line in stored.lines:
                lines.setdefault(line.id, line)
        return line_status_notices(lines.values(), now)


class StoredTflStationSource(StoredTflNoticeSource):
    """Lifts out of order, closed entrances and the like at the stops themselves, as stored by stop."""

    name = "TfL stations"

    def __init__(self, session: requests.Session, stop_ids: list[str]) -> None:
        """Read what's wrong at the TfL stops `stop_ids`; `session` is the board's (unused)."""
        super().__init__(session)
        self.stop_ids = stop_ids

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        stored = self._read_all([keys.tfl_stations(stop_id) for stop_id in self.stop_ids], StoredStationDisruptions)
        return station_notices([point for stop in stored for point in stop.disruptions], now)


class StoredTflRoadSource(StoredTflNoticeSource):
    """Serious and severe road incidents near the postcode, as stored by place. London only, as TfL's are."""

    name = "TfL roads"

    def __init__(self, session: requests.Session, postcode: str) -> None:
        """Read the incidents near `postcode` (the broker's, as the provider was subscribed to it)."""
        super().__init__(session)
        self.postcode = postcode

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        stored = reader.read(reader.require_store(), keys.tfl_roads(self.postcode), StoredRoadDisruptions)
        if stored is None:
            # Not naming the key: the board logs this, and the key has the postcode in it.
            msg = "No TfL road disruptions in the store for the board's postcode"
            raise NoStoredTflNoticesError(msg)
        return road_notices(stored.disruptions)
