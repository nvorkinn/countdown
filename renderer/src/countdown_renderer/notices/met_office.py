"""Weather warnings for a UK postcode, from the Met Office's RSS feeds.

provider-met-office fetches the feed of each subscribed postcode's region (met_office_feed.region_for) and stores its
items under the postcode (countdown_core.store.met_office); the notice board reads them from there
(StoredMetOfficeWarningsSource) and makes its notices from them when it draws (warning_notices).
"""

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, override
from zoneinfo import ZoneInfo

from countdown_core.i18n import N_, _, format_date
from countdown_core.notices.met_office_feed import region_for
from countdown_core.store import keys, reader
from countdown_core.store.met_office import StoredMetOfficeWarnings
from countdown_renderer.notices.notice import Notice, NoticeSource, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    import redis
    import requests

    from countdown_core.store.met_office import FeedItem
    from countdown_core.tfl.models import Postcode

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


class NoStoredWarningsError(reader.NoStoredDataError):
    """The store has no warnings for the postcode: provider-met-office is down, or hasn't fetched them yet."""


class StoredMetOfficeWarningsSource(NoticeSource):
    """The Met Office's weather warnings for the postcode's region, read from the store (`v1:notices:met-office:`).

    provider-met-office writes them there, from the feed of the region the postcode is in. The notices are made from
    the stored items each time it's read, so their text is in the screen's language and their days are as of then.
    """

    name = "Met Office warnings"

    def __init__(
        self, session: requests.Session, location: Postcode, postcode: str, store: redis.Redis | None = None
    ) -> None:
        """Read the warnings for `postcode` (the board's, as configured), which is at `location`, from `store`.

        `store` is the renderer's (reader.require_store()) unless given, asked for only when the warnings are read,
        so a renderer without one fails this source alone, not the board.
        """
        super().__init__(session)
        # The feed the provider reads for this postcode, as the board placed it. Never logged: it says roughly where
        # the board is.
        self.region = region_for(location)
        self.key = keys.met_office(postcode)
        self.store = store

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        store = self.store if self.store is not None else reader.require_store()
        stored = reader.read(store, self.key, StoredMetOfficeWarnings)
        if stored is None:
            # Not the key: the board logs this, and where the board is stays out of the logs.
            msg = "No Met Office warnings in the store for the board's postcode"
            raise NoStoredWarningsError(msg)
        return warning_notices(stored.items, now)
