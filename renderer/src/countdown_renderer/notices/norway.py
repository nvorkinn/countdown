"""The notice board's sources for a Norwegian postcode: its public holidays (worked out, not fetched) and its warnings.

The warnings are MET Norway's MetAlerts, from the store: the MetAlerts provider fetches them for all of Norway, and the
board picks out its place's. What the providers ask MET and Kartverket, and where, is norway_feed's; the wording of
both is countdown_shared's too (notices/wording/norway.py).
"""

import logging
from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource
from countdown_shared.notices.wording.norway import (
    covers,
    metalerts_language,
    norwegian_holiday_notices,
    norwegian_holidays,
    parse_metalerts,
    warnings_at,
)
from countdown_shared.store import keys, reader
from countdown_shared.store.metalerts import StoredMetAlerts

if TYPE_CHECKING:
    from datetime import datetime

    import requests

    from countdown_shared.tfl.models import Postcode

__all__ = [
    "NoStoredMetAlertsError",
    "NorwegianHolidaySource",
    "StoredMetAlertsSource",
    "covers",
    "norwegian_holidays",
    "parse_metalerts",
    "warnings_at",
]

logger = logging.getLogger(__name__)


class NorwegianHolidaySource(NoticeSource):
    """The next Norwegian public holiday, once it's less than a month away, like StoredBankHolidaySource.

    Worked out, not fetched, so it needs no network.
    """

    name = "Public holidays"
    needs_network = False

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        return norwegian_holiday_notices(now)


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
