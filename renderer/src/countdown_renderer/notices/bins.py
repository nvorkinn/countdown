"""The notice board's bin collection notice, for a Norwegian board with its household's address set.

The bins provider stores each address's collection days, from Binable, under v1:bins:<SHA1 of the address>; this
reads them and shows the next collection day, and what's emptied then, once it's less than a week away. Worded from
countdown-api's messages (notices.messages), as the other Norwegian notices are.
"""

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, override

from countdown_renderer.notices.calendar import when
from countdown_renderer.notices.messages import say
from countdown_renderer.notices.norway import NORWAY_TIME
from countdown_renderer.notices.notice import Notice, NoticeSource, Severity
from countdown_shared.store import keys, reader
from countdown_shared.store.bins import StoredBinCollections

if TYPE_CHECKING:
    import requests

# How far ahead a collection is shown: the week before it, so it's there for the evening the bins go out.
AHEAD = timedelta(days=7)


class NoStoredBinCollectionsError(reader.NoStoredDataError):
    """The store has no collection days for the address: the bins provider is down, or hasn't fetched them yet."""


class StoredBinCollectionSource(NoticeSource):
    """The household's next bin collection day, and the kinds of waste emptied then, from the store."""

    name = "Bin collection"

    def __init__(self, session: requests.Session, address: str) -> None:
        """Read `address`'s days (its v1:sub:address member). `session` is the board's, unused: this reads the store."""
        super().__init__(session)
        self.address = address

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Read the days; raises NoStoredBinCollectionsError if the store has none, so the board leaves them out.

        Raises StoreNotConfiguredError for a renderer without a store, and whatever Redis raises if it can't be read.
        """
        stored = reader.read(reader.require_store(), keys.bins(self.address), StoredBinCollections)
        if stored is None:
            # Not the key: it's the address's hash, but the logs needn't have even that.
            msg = "No bin collection days in the store for the board's address"
            raise NoStoredBinCollectionsError(msg)
        return bin_notices(stored, now)


def bin_notices(stored: StoredBinCollections, now: datetime) -> list[Notice]:
    """Return the notice for the next collection day in `stored`, if it's from today to a week ahead, else none."""
    today = now.astimezone(NORWAY_TIME).date()
    upcoming = {
        collection.fraction: min(day for day in collection.dates if day >= today)
        for collection in stored.collections
        if any(day >= today for day in collection.dates)
    }
    if not upcoming:
        return []
    day = min(upcoming.values())
    if day - today >= AHEAD:
        return []
    # In Binable's order, each kind of waste once: its less frequent and container collections are worded alike.
    names = list(dict.fromkeys(say("notice.bins.fraction", fraction=f) for f, d in upcoming.items() if d == day))
    ends = datetime.combine(day + timedelta(days=1), time(), tzinfo=NORWAY_TIME)
    text = say("notice.bins.text", when=when(day, today), fractions=", ".join(names))
    return [Notice(say("notice.bins.tag"), Severity.REMINDER, text, ends)]
