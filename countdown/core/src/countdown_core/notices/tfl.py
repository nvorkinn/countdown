"""TfL's disruptions: on the lines and at the stations the screen shows, and on the roads near it."""

import json
import logging
import re
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING, override

from pydantic import TypeAdapter

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.i18n import _
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.tfl.models import DisruptedPoint, Line, Postcode, RoadDisruption, StopPointUnion
from countdown_core.tfl.tfl_client import _find_stop_child

if TYPE_CHECKING:
    import requests

    from countdown_api.models import Tfl

logger = logging.getLogger(__name__)

TFL_URL = "https://api.tfl.gov.uk"

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

ROAD_RADIUS_KM = 3
ROAD_SEVERITY = {"Severe": Severity.WARNING, "Serious": Severity.INFO}

LineStatuses = TypeAdapter(list[Line])
DisruptedPoints = TypeAdapter(list[DisruptedPoint])
RoadDisruptions = TypeAdapter(list[RoadDisruption])


class TflSource(NoticeSource):
    """A notice source asking TfL's unified API, with the screen's app key if it has one."""

    def __init__(self, session: requests.Session, config: Tfl) -> None:
        """Fetch over `session`, with `config`'s app key."""
        super().__init__(session)
        self.params = {"app_key": config.app_key} if config.app_key else {}

    def _get(self, path: str, **params: str) -> bytes:
        response = self.session.get(f"{TFL_URL}{path}", params=self.params | params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        # The raw body, before pydantic drops the fields the models don't declare: turn on
        # debug logging for this module to see everything TfL actually sends.
        logger.debug("GET %s: %s", path, response.text)
        return response.content


class TflLineStatusSource(TflSource):
    """Delays, closures and suspensions on the lines and bus routes serving the stops."""

    name = "TfL line status"
    refresh_interval = timedelta(minutes=5)

    def __init__(self, session: requests.Session, config: Tfl) -> None:
        """Fetch over `session`, for the lines serving `config`'s stops."""
        super().__init__(session, config)
        self.stop_ids = config.stop_ids
        self.line_ids: list[str] | None = None  # looked up on first fetch

    def _resolve_line_ids(self) -> list[str]:
        line_ids: list[str] = []
        for stop_id in self.stop_ids:
            stop = _find_stop_child(
                TypeAdapter(StopPointUnion).validate_json(self._get(f"/StopPoint/{stop_id}")), stop_id
            )
            if stop is None:
                logger.warning("TfL stop %s has no lines to watch", stop_id)
                continue
            line_ids += [line.id for line in stop.lines if line.id not in line_ids]
        return line_ids

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        if self.line_ids is None:
            self.line_ids = self._resolve_line_ids()
        if not self.line_ids:
            return []
        # Keyed by reason: a bus closure is reported word for word on every route it
        # affects, so it's one notice, not one per route.
        notices: dict[str | tuple[str, str], Notice] = {}
        for line in LineStatuses.validate_json(self._get(f"/Line/{','.join(self.line_ids)}/Status")):
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
                    valid_until = (
                        None if None in (valid_until, seen.valid_until) else max(valid_until, seen.valid_until)
                    )
                    if seen.source != tag:
                        tag = _("Buses") if line.mode_name == "bus" else "TfL"
                notices[key] = Notice(tag, severity, text, valid_until)
        return list(notices.values())


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


class TflStationSource(TflSource):
    """Lifts out of order, closed entrances and the like at the stops themselves."""

    name = "TfL stations"
    refresh_interval = timedelta(minutes=10)

    def __init__(self, session: requests.Session, config: Tfl) -> None:
        """Fetch over `session`, for `config`'s stops."""
        super().__init__(session, config)
        self.stop_ids = config.stop_ids

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        notices: list[Notice] = []
        seen: set[str] = set()
        for point in DisruptedPoints.validate_json(self._get(f"/StopPoint/{','.join(self.stop_ids)}/Disruption")):
            if point.to_date is not None and point.to_date < now:
                continue
            # Descriptions lead with the station's name in capitals ("ELEPHANT & CASTLE
            # UNDERGROUND STATION: ..."); swap it for a short one that reads better.
            _, colon, rest = point.description.partition(":")
            detail = rest.strip() if colon else point.description.strip()
            text = f"{_short_station_name(point.common_name)}: {detail}"
            # TfL often lists the same notice more than once, for different dates.
            if text not in seen:
                seen.add(text)
                notices.append(Notice("TfL", Severity.INFO, text, point.to_date))
        return notices


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


class TflRoadSource(TflSource):
    """Serious and severe road incidents (collisions, closures, burst water mains) near the postcode.

    London only: TfL doesn't know about roads anywhere else.
    """

    name = "TfL roads"
    refresh_interval = timedelta(minutes=10)

    def __init__(self, session: requests.Session, config: Tfl, location: Postcode) -> None:
        """Fetch over `session` the incidents within ROAD_RADIUS_KM of `location`."""
        super().__init__(session, config)
        self.location = location

    def _is_nearby(self, disruption: RoadDisruption) -> bool:
        try:
            lon, lat = json.loads(disruption.point or "")
        except ValueError, TypeError:
            return False
        return _distance_km(self.location.latitude, self.location.longitude, lat, lon) <= ROAD_RADIUS_KM

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        disruptions = RoadDisruptions.validate_json(
            self._get("/Road/all/Disruption", severities=",".join(ROAD_SEVERITY))
        )
        return [
            Notice(_("Roads"), ROAD_SEVERITY[d.severity], f"{d.category}: {d.comments}", d.end_date_time)
            for d in disruptions
            if d.severity in ROAD_SEVERITY and self._is_nearby(d)
        ]
