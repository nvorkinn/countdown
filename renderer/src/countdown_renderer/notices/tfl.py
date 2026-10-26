"""TfL's disruptions: on the lines and at the stations the screen shows, and on the roads near it.

TfL is asked once for every screen, by the TfL notices provider (countdown_providers.tfl_notices), which stores its
answers (store/tfl_notices.py): by stop, the status of the lines serving it and what's wrong at it; by place, the road
incidents near it. These sources read them and make the notices as the board polls, so the tags are in the screen's
language and what's current is judged as of drawing.
"""

import re
from typing import TYPE_CHECKING, override

from countdown_core.i18n import _
from countdown_core.notices.tfl_feed import ROAD_SEVERITIES
from countdown_core.store import keys, reader
from countdown_core.store.tfl_notices import StoredLineStatus, StoredRoadDisruptions, StoredStationDisruptions
from countdown_renderer.notices.notice import Notice, NoticeSource, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    import requests

    from countdown_core.store.models import Stored
    from countdown_core.tfl.models import DisruptedPoint, Line, RoadDisruption

# The line statuses worth a notice, by TfL's name for them (/Line/Meta/Severity has the
# full list, the same for every mode). Everything else -- minor delays, bus diversions
# ("Special Service"), reduced service, "Service Closed" after the last train -- isn't.
CURRENT_STATUSES = {
    "Suspended": Severity.SEVERE,
    "Part Suspended": Severity.WARNING,
    "Severe Delays": Severity.WARNING,
    "Issues Reported": Severity.INFO,
}
PLANNED_STATUSES = {"Part Closure", "Planned Closure", "Closed", "Not Running"}

# Words a sentence can't end on, so "St. Paul's" isn't taken for the end of one.
ABBREVIATIONS = {"st", "rd", "ave", "mt", "no"}

ROAD_SEVERITY = dict(zip(ROAD_SEVERITIES, (Severity.WARNING, Severity.INFO), strict=True))


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


def line_status_notices(lines: Iterable[Line], now: datetime) -> list[Notice]:
    """Make the notices for TfL's statuses of `lines`: the ones worth a notice that apply at `now`."""
    # Keyed by reason: a bus closure is reported word for word on every route it affects, so it's one notice, not one
    # per route.
    notices: dict[str | tuple[str, str], Notice] = {}
    for line in lines:
        for status in line.line_statuses:
            if status.description in CURRENT_STATUSES:
                severity = CURRENT_STATUSES[status.description]
            elif status.description in PLANNED_STATUSES:
                severity = Severity.PLANNED
            else:
                continue
            current = [p for p in status.validity_periods if p.from_date <= now <= p.to_date]
            if status.validity_periods and not current:
                continue  # e.g. a planned closure later this week
            tag = _("Bus {route}").format(route=line.name) if line.mode_name == "bus" else line.name
            headline = _headline(status.reason, line.name)
            text = headline or status.description
            key = headline or (tag, status.description)
            valid_until = max((p.to_date for p in current), default=None)
            if key in notices:
                seen = notices[key]
                severity = min(severity, seen.severity)
                if valid_until is not None and seen.valid_until is not None:
                    valid_until = max(valid_until, seen.valid_until)
                else:
                    valid_until = None
                if seen.source != tag:
                    tag = _("Buses") if line.mode_name == "bus" else "TfL"
            notices[key] = Notice(tag, severity, text, valid_until)
    return list(notices.values())


def station_notices(points: Iterable[DisruptedPoint], now: datetime) -> list[Notice]:
    """Make the notices for what TfL says is wrong at the stops (`points`), leaving out what's over by `now`."""
    notices: list[Notice] = []
    seen: set[str] = set()
    for point in points:
        if point.to_date is not None and point.to_date < now:
            continue
        # Descriptions lead with the station's name in capitals ("ELEPHANT & CASTLE UNDERGROUND STATION: ..."); swap
        # it for a short one that reads better.
        _, colon, rest = point.description.partition(":")
        detail = rest.strip() if colon else point.description.strip()
        text = f"{_short_station_name(point.common_name)}: {detail}"
        # TfL often lists the same notice more than once, for different dates.
        if text not in seen:
            seen.add(text)
            notices.append(Notice("TfL", Severity.INFO, text, point.to_date))
    return notices


def road_notices(disruptions: Iterable[RoadDisruption]) -> list[Notice]:
    """Make the notices for the serious and severe road disruptions among `disruptions` (already the nearby ones)."""
    return [
        Notice(_("Roads"), ROAD_SEVERITY[d.severity], f"{d.category}: {d.comments}", d.end_date_time)
        for d in disruptions
        if d.severity in ROAD_SEVERITY
    ]


def _headline(reason: str | None, line_name: str) -> str:
    """Cut a status reason to its first sentence (the rest never fits), dropping the line name at its start.

    Most reasons start with one ("CENTRAL LINE: ..."). A location prefix, like a bus diversion's
    "WATERLOO ROAD, Southwark:", is kept.
    """
    text = " ".join((reason or "").split())
    prefix, colon, rest = text.partition(":")
    lowered = prefix.lower()
    if colon and (line_name.lower() in lowered or lowered.endswith(("line", "railway", "overground"))):
        text = rest.strip()
    for match in re.finditer(r"\.\s+(?=[A-Z])", text):
        last_word = text[: match.start()].rsplit(" ", 1)[-1].lower()
        if last_word not in ABBREVIATIONS:
            text = text[: match.start() + 1]
            break
    return text[:1].upper() + text[1:]


def _short_station_name(common_name: str) -> str:
    return re.sub(r" (Underground|Rail|DLR)? ?Station$", "", common_name).strip()
