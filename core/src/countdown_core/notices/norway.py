"""The notice board's sources for a Norwegian postcode: its public holidays (worked out here) and weather warnings.

The warnings are MET Norway's MetAlerts, from the store: the MetAlerts provider fetches them for all of Norway, and the
board picks out its place's. What the providers ask MET and Kartverket, and where, is norway_feed's.
"""

import itertools
import logging
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, override
from zoneinfo import ZoneInfo

from countdown_core.i18n import N_, _, format_date, language
from countdown_core.notices.calendar import when
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.store import keys, reader
from countdown_core.store.metalerts import StoredMetAlerts

if TYPE_CHECKING:
    import requests

    from countdown_core.tfl.models import Postcode

logger = logging.getLogger(__name__)

NORWAY_TIME = ZoneInfo("Europe/Oslo")


def _easter(year: int) -> date:
    """Easter Sunday, by the anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 -- the algorithm's own name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def norwegian_holidays(year: int) -> list[tuple[date, str]]:
    """List Norway's public holidays (helligdager and the two statutory days, 1 and 17 May), in date order.

    Their English names are marked for translation.
    """
    easter = _easter(year)
    holidays = [
        (date(year, 1, 1), N_("New Year's Day")),
        (easter - timedelta(days=3), N_("Maundy Thursday")),
        (easter - timedelta(days=2), N_("Good Friday")),
        (easter, N_("Easter Sunday")),
        (easter + timedelta(days=1), N_("Easter Monday")),
        (date(year, 5, 1), N_("Labour Day")),
        (date(year, 5, 17), N_("Constitution Day")),
        (easter + timedelta(days=39), N_("Ascension Day")),
        (easter + timedelta(days=49), N_("Whit Sunday")),
        (easter + timedelta(days=50), N_("Whit Monday")),
        (date(year, 12, 25), N_("Christmas Day")),
        (date(year, 12, 26), N_("Boxing Day")),
    ]
    return sorted(holidays)


class NorwegianHolidaySource(NoticeSource):
    """The next Norwegian public holiday, once it's less than a month away, like StoredBankHolidaySource.

    Worked out here, so it needs no network.
    """

    name = "Public holidays"
    needs_network = False

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        today = now.astimezone(NORWAY_TIME).date()
        upcoming = [
            (day, name)
            for year in (today.year, today.year + 1)
            for day, name in norwegian_holidays(year)
            if today <= day < today + timedelta(days=31)
        ]
        if not upcoming:
            return []
        day, name = upcoming[0]
        ends = datetime.combine(day + timedelta(days=1), time(), tzinfo=NORWAY_TIME)
        return [Notice(_("Public holiday"), Severity.REMINDER, f"{when(day, today)}: {_(name)}", ends)]


SEVERITY_BY_COLOUR = {"red": Severity.SEVERE, "orange": Severity.WARNING, "yellow": Severity.INFO}
WARNING_BY_COLOUR = {
    "red": N_("Red warning: {hazard}"),
    "orange": N_("Orange warning: {hazard}"),
    "yellow": N_("Yellow warning: {hazard}"),
}


def _colour(properties: dict) -> str | None:
    """Read the warning's level: "yellow", "orange" or "red".

    From riskMatrixColor, or awareness_level ("2; yellow; Moderate").
    """
    colour = (properties.get("riskMatrixColor") or "").lower()
    if colour in SEVERITY_BY_COLOUR:
        return colour
    parts = [part.strip().lower() for part in (properties.get("awareness_level") or "").split(";")]
    return next((part for part in parts if part in SEVERITY_BY_COLOUR), None)


def parse_metalerts(payload: dict, now: datetime) -> list[Notice]:
    """MetAlerts' current warnings as Notices: "Yellow warning: strong wind until Fri 21:00"."""
    notices = []
    for feature in payload.get("features") or []:
        properties = feature.get("properties") or {}
        colour = _colour(properties)
        hazard = properties.get("eventAwarenessName") or properties.get("event") or ""
        if colour is None or not hazard:
            continue
        interval = (feature.get("when") or {}).get("interval") or []
        valid_until = datetime.fromisoformat(interval[-1]) if interval else None
        if valid_until is not None and valid_until < now:
            continue
        text = _(WARNING_BY_COLOUR[colour]).format(hazard=hazard.lower())
        local = valid_until.astimezone(NORWAY_TIME) if valid_until is not None else None
        if local is not None:
            # Like the Met Office's: "until Fri 21:00", the board adding the time in the screen's clock format.
            text += " " + _("until {day}").format(day=format_date(local.date(), "EEE"))
        notices.append(Notice("MET", SEVERITY_BY_COLOUR[colour], text, valid_until, display_time=local))
    return notices


def metalerts_language() -> str:
    """Return the `lang` MetAlerts words its warnings in for this screen: "no" for one in Norwegian, else "en"."""
    return "no" if language() == "nb" else "en"


def _inside(polygon: list, longitude: float, latitude: float) -> bool:
    """Whether a point is in a GeoJSON polygon: in its outer ring and none of its holes.

    By counting the edges of every ring that a ray from the point crosses (even-odd), so a point in a hole is out.
    """
    inside = False
    for ring in polygon:
        points = [(point[0], point[1]) for point in ring]  # leaving out any altitude
        if not points:
            continue
        for (x1, y1), (x2, y2) in itertools.pairwise([*points, points[0]]):
            if (y1 > latitude) != (y2 > latitude) and longitude < x1 + (latitude - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def covers(geometry: dict | None, latitude: float, longitude: float) -> bool:
    """Whether a warning's area covers a point: its GeoJSON geometry, a Polygon or MultiPolygon (longitude first).

    A warning without one covers nowhere: MET gives every warning its area.
    """
    if not geometry:
        return False
    coordinates = geometry.get("coordinates") or []
    match geometry.get("type"):
        case "Polygon":
            polygons = [coordinates]
        case "MultiPolygon":
            polygons = coordinates
        case _:
            return False
    return any(_inside(polygon, longitude, latitude) for polygon in polygons)


def warnings_at(payload: dict, location: Postcode) -> dict:
    """Cut MetAlerts' answer for all of Norway down to the warnings whose area covers `location`.

    What MET answers when asked for the place itself (current.json?lat=...&lon=..., "all alerts covered by the search
    location"), with the location at four decimals.
    """
    latitude, longitude = round(location.latitude, 4), round(location.longitude, 4)
    features = [
        feature for feature in payload.get("features") or [] if covers(feature.get("geometry"), latitude, longitude)
    ]
    return {**payload, "features": features}


class NoStoredMetAlertsError(reader.NoStoredDataError):
    """The store has no MET weather warnings: the MetAlerts provider is down, or hasn't fetched them yet."""


class StoredMetAlertsSource(NoticeSource):
    """MET Norway's weather warnings for the postcode's location, in the screen's language, from the store.

    `v1:notices:metalerts`, which the MetAlerts provider fills with MET's answers for all of Norway; this picks out the
    warnings covering the location and words them as it reads them, so in the screen's language as of then.
    """

    name = "MET weather warnings"

    def __init__(self, session: requests.Session, location: Postcode) -> None:
        """Pick out the warnings for `location`. `session` is the board's, unused: this reads the store, not MET."""
        super().__init__(session)
        self.location = location

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Read the warnings; raises NoStoredMetAlertsError if the store has none, so the board leaves them out.

        Raises StoreNotConfiguredError for a renderer without a store, and whatever Redis raises if it can't be read.
        """
        stored = reader.read(reader.require_store(), keys.metalerts(), StoredMetAlerts)
        if stored is None:
            msg = f"No MET weather warnings in the store ({keys.metalerts()})"
            raise NoStoredMetAlertsError(msg)
        answer = stored.no if metalerts_language() == "no" else stored.en
        return parse_metalerts(warnings_at(answer, self.location), now)
