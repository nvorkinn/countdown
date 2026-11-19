"""Working out a stop's next departures from TfL's timetables: which schedule runs on a day, and when its trains leave.

TfL's /Line/{id}/Timetable/{stop id} has a schedule per kind of day ("Monday - Thursday", "Friday", "Saturday (also
Good Friday)", "Sunday", or a bus's "Monday to Friday"), each a list of hour:minute departures from the stop, the hours
running past 23 for a journey after midnight that belongs to the day before (Friday's 27:30 is 03:30 on Saturday). Bank
holidays run Sunday's (Good Friday a schedule's that says so), and Christmas Day has no service at all.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from countdown_shared.store.timetable import TimetabledDeparture

if TYPE_CHECKING:
    from collections.abc import Mapping

    from countdown_shared.tfl.models import Schedule, TimetableResponse

LONDON = ZoneInfo("Europe/London")
# How many departures to keep per line and destination: a board shows a few rows, and the first few are all it needs.
PER_DESTINATION = 4
# How far ahead to look: the longest a line is shut (Christmas Day) is under a day.
HORIZON = timedelta(hours=30)
SUNDAY = 6
NO_SERVICE = "Christmas Day"
GOOD_FRIDAY = "Good Friday"

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY = r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*"
_RANGE = re.compile(rf"{_DAY}\s*(?:-|\u2013|to)\s*{_DAY}")
_ONE = re.compile(rf"\b{_DAY}\b")
# TfL's names end with what kind of stop it is, which a board needn't say: "Morden Underground Station" is "Morden".
_STATION_SUFFIX = re.compile(r"\s+(Underground|DLR|Rail)\s+Station$")


def days_of(name: str) -> frozenset[int]:
    """Return the weekdays (0 is Monday) a schedule's name says it runs on; none for a name that names no day."""
    # What's in brackets is an exception ("also Good Friday"), not a day it always runs.
    plain = re.sub(r"\(.*?\)", "", name.lower())
    days: set[int] = set()
    for first, last in _RANGE.findall(plain):
        start, end = _index(first), _index(last)
        days.update((start + offset) % 7 for offset in range((end - start) % 7 + 1))
    for one in _ONE.findall(_RANGE.sub("", plain)):
        days.add(_index(one))
    return frozenset(days)


def _index(prefix: str) -> int:
    return next(index for index, day in enumerate(_DAYS) if day.startswith(prefix))


def schedule_for(schedules: list[Schedule], day: date, holidays: Mapping[date, str]) -> Schedule | None:
    """Return the schedule that runs on `day`, given England and Wales's bank holidays (date to title); None if none."""
    holiday = holidays.get(day)
    if holiday == NO_SERVICE:
        return None
    if holiday == GOOD_FRIDAY:
        for schedule in schedules:
            if GOOD_FRIDAY.lower() in schedule.name.lower():
                return schedule
    weekday = SUNDAY if holiday else day.weekday()
    return next((schedule for schedule in schedules if weekday in days_of(schedule.name)), None)


def short_name(name: str) -> str:
    """Return a stop's name without TfL's kind of station on the end ("Morden Underground Station" is "Morden")."""
    return _STATION_SUFFIX.sub("", name)


def next_departures(
    response: TimetableResponse, stop_id: str, now: datetime, holidays: Mapping[date, str]
) -> list[TimetabledDeparture]:
    """Return the timetable's departures from `stop_id` from `now` on, the first PER_DESTINATION per destination.

    Nothing when the timetable is another stop's (TfL's answer for a direction the line doesn't run from this one).
    """
    timetable = response.timetable
    if timetable is None or timetable.departure_stop_id != stop_id or not response.line_id:
        return []
    line = response.line_name or response.line_id
    names = {stop.id: stop.name for stop in response.stops}
    today = now.astimezone(LONDON).date()
    found: list[TimetabledDeparture] = []
    for route in timetable.routes:
        # Where each of the route's ways ends; none that end here, as a train ending its journey isn't a departure.
        ends = {
            interval.id: interval.intervals[-1].stop_id
            for interval in route.station_intervals
            if interval.intervals and interval.intervals[-1].stop_id != stop_id
        }
        # Yesterday's too: its journeys after midnight (25:10) are early this morning.
        for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
            schedule = schedule_for(route.schedules, day, holidays)
            if schedule is None:
                continue
            for journey in schedule.known_journeys:
                end = ends.get(journey.interval_id)
                if end is None:
                    continue
                departs_at = _at(day, journey.hour, journey.minute)
                if now <= departs_at <= now + HORIZON:
                    found.append(
                        TimetabledDeparture(
                            line=line,
                            line_id=response.line_id,
                            destination=short_name(names.get(end, end)),
                            departs_at=departs_at,
                        )
                    )
    found.sort(key=lambda departure: departure.departs_at)
    kept: list[TimetabledDeparture] = []
    counts: dict[str, int] = {}
    for departure in found:
        counts[departure.destination] = counts.get(departure.destination, 0) + 1
        if counts[departure.destination] <= PER_DESTINATION:
            kept.append(departure)
    return kept


def _at(day: date, hour: int, minute: int) -> datetime:
    """Return hour:minute of `day`'s schedule, London time (the hour can run past 23 into the next day), in UTC."""
    wall = datetime.combine(day, time()) + timedelta(hours=hour, minutes=minute)
    return wall.replace(tzinfo=LONDON).astimezone(UTC)
