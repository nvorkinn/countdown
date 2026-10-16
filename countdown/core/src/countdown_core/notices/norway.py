"""The notice board's sources for a Norwegian postcode.

Where it is (Kartverket), public holidays (worked out here) and weather warnings (MET Norway's MetAlerts).
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, override
from zoneinfo import ZoneInfo

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.i18n import N_, _, format_date, language
from countdown_core.notices.calendar import when
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.tfl.models import Postcode

if TYPE_CHECKING:
    import requests

logger = logging.getLogger(__name__)

NORWAY_TIME = ZoneInfo("Europe/Oslo")
# Kartverket's address search: free, no key. A postcode's addresses all carry its place name and municipality.
ADDRESSES_URL = "https://ws.geonorge.no/adresser/v1/sok"
METALERTS_URL = "https://api.met.no/weatherapi/metalerts/2.0/current.json"
# api.met.no refuses requests that don't say who's asking (https://api.met.no/doc/TermsOfService).
MET_HEADERS = {"User-Agent": "countdown https://github.com/nvorkinn/pi-dashboard"}


def lookup_norwegian_postcode(session: requests.Session, postcode: str) -> Postcode | None:
    """Look up where a Norwegian postcode is, as a Postcode with its municipality as the district.

    None if Kartverket has no address with it; raises if Kartverket can't be reached, so the caller can try again
    later.
    """
    response = session.get(
        ADDRESSES_URL,
        params={"postnummer": postcode.strip(), "treffPerSide": 1, "utkoordsys": 4258},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    addresses = response.json().get("adresser") or []
    if not addresses:
        return None
    address = addresses[0]
    point = address.get("representasjonspunkt") or {}
    if point.get("lat") is None or point.get("lon") is None:
        return None
    return Postcode(
        postcode=address.get("postnummer") or postcode,
        latitude=point["lat"],
        longitude=point["lon"],
        country="Norway",
        admin_district=(address.get("kommunenavn") or "").title() or None,
    )


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
    """The next Norwegian public holiday, once it's less than a month away, like BankHolidaySource.

    Worked out here, so it needs no network.
    """

    name = "Public holidays"
    refresh_interval = timedelta(minutes=10)  # so "Tomorrow" becomes "Today" on time
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


class MetAlertsSource(NoticeSource):
    """MET Norway's weather warnings for the postcode's location, in the screen's language."""

    name = "MET weather warnings"
    refresh_interval = timedelta(minutes=10)

    def __init__(self, session: requests.Session, location: Postcode) -> None:
        """Fetch over `session` the warnings for `location`."""
        super().__init__(session)
        self.params = {"lat": round(location.latitude, 4), "lon": round(location.longitude, 4)}

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        response = self.session.get(
            METALERTS_URL,
            params=self.params | {"lang": "no" if language() == "nb" else "en"},
            headers=MET_HEADERS,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return parse_metalerts(response.json(), now)
