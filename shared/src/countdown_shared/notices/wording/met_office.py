"""The wording of the Met Office's weather warnings for a UK postcode.

Made from the items of its region's RSS feed, as provider-met-office stored them (store/met_office.py), as of the
moment asked about and in the screen's language. Reading the store is the caller's.
"""

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from countdown_shared.i18n import N_, _, format_date
from countdown_shared.notices.wording.notice import Notice, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from countdown_shared.store.met_office import FeedItem

UK_TIME = ZoneInfo("Europe/London")

SEVERITY_BY_COLOUR = {"red": Severity.SEVERE, "amber": Severity.WARNING, "yellow": Severity.INFO}
WARNING_BY_COLOUR = {
    "red": N_("Red warning: {hazard}"),
    "amber": N_("Amber warning: {hazard}"),
    "yellow": N_("Yellow warning: {hazard}"),
}
TITLE_PATTERN = re.compile(r"^(Red|Amber|Yellow) warning of (.+?) affecting", re.IGNORECASE)
# e.g. "valid from 0600 Wed 25 Sep to 2100 Wed 25 Sep"
VALID_TO_PATTERN = re.compile(r"\bto (\d{4}) \w{3} (\d{1,2}) (\w{3})\b")


def _valid_until(description: str, now: datetime) -> datetime | None:
    """Read the end of a warning's validity from the "valid from ... to 2100 Wed 25 Sep" in its description.

    The feed leaves the year out, so it's this year's date unless that's long gone (a warning into January, read in
    December).
    """
    match = VALID_TO_PATTERN.search(description)
    if not match:
        return None
    time_text, day, month = match.groups()
    local_now = now.astimezone(UK_TIME)
    try:
        until = datetime.strptime(f"{local_now.year} {month} {day} {time_text}", "%Y %b %d %H%M").replace(
            tzinfo=UK_TIME
        )
    except ValueError:
        return None
    if until < local_now - timedelta(days=180):
        until = until.replace(year=until.year + 1)
    return until


def warning_notices(items: Iterable[FeedItem], now: datetime) -> list[Notice]:
    """Turn the feed's items, as the provider stored them, into Notices, as of `now` and in the screen's language.

    Reads the colour and hazard out of titles like "Amber warning of wind affecting London & South East England"; a
    title in any other shape is shown as it is rather than dropped, and an item with no title is left out.
    """
    notices = []
    for item in items:
        title = item.title.strip()
        if not title:
            continue
        valid_until = _valid_until(item.description.strip(), now)
        match = TITLE_PATTERN.match(title)
        if match:
            colour, hazard = match.group(1).lower(), match.group(2).lower()
            text = _(WARNING_BY_COLOUR[colour]).format(hazard=hazard)
            severity = SEVERITY_BY_COLOUR[colour]
        else:
            text, severity = title, Severity.WARNING
        if valid_until is not None:
            # The board adds the time, in the screen's clock format: "until Fri 21:00".
            text += " " + _("until {day}").format(day=format_date(valid_until.date(), "EEE"))
        notices.append(Notice("Met Office", severity, text, valid_until, display_time=valid_until))
    return notices
