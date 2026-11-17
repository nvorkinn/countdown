"""The wording of TfL's disruptions: on the lines, at the stations and on the roads.

Made from TfL's answers as the TfL notices provider stored them (store/tfl_notices.py), as of the moment asked about
and in the screen's language. Reading the store is the caller's.
"""

import re
from typing import TYPE_CHECKING

from countdown_shared.i18n import _
from countdown_shared.notices.tfl_feed import ROAD_SEVERITIES
from countdown_shared.notices.wording.notice import Notice, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from countdown_shared.tfl.models import DisruptedPoint, Line, RoadDisruption

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
