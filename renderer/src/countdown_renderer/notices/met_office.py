"""Weather warnings for a UK postcode, from the Met Office's RSS feeds.

provider-met-office fetches the feed of each subscribed postcode's region (met_office_feed.region_for) and stores its
items under the postcode (countdown_shared.store.met_office); the notice board reads them from there
(StoredMetOfficeWarningsSource) and makes its notices from them when it draws (warning_notices, countdown_shared's:
notices/wording/met_office.py).
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource
from countdown_shared.notices.met_office_feed import region_for
from countdown_shared.notices.wording.met_office import warning_notices
from countdown_shared.store import keys, reader
from countdown_shared.store.met_office import StoredMetOfficeWarnings

if TYPE_CHECKING:
    from datetime import datetime

    import redis
    import requests

    from countdown_shared.tfl.models import Postcode

__all__ = ["NoStoredWarningsError", "StoredMetOfficeWarningsSource", "warning_notices"]


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
