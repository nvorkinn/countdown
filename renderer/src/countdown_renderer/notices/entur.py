"""Disruptions at the Norwegian stops the screen shows departures for.

These are Entur's situation messages (Ruter's and every other operator's), on the stops themselves and on the lines
leaving them. provider-entur-situations asks Entur for each stop's (entur_feed.query_situations) and stores the answer
(`v1:notices:entur:<stop id>`); the notice board reads it from there (StoredEnturSituationsSource) and makes the
notices as it draws (situation_notices, countdown_shared's: notices/wording/entur.py).
"""

import logging
from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource
from countdown_shared.notices.wording.entur import situation_notices
from countdown_shared.store import keys, reader
from countdown_shared.store.situations import StoredSituations

if TYPE_CHECKING:
    from datetime import datetime

    import redis

logger = logging.getLogger(__name__)


class NoStoredSituationsError(reader.NoStoredDataError):
    """None of the screen's Entur stops has its situations in the store: their provider is down, or not there yet."""


class StoredEnturSituationsSource(NoticeSource):
    """The disruptions at the screen's Entur stops, from the store rather than from Entur.

    provider-entur-situations fetches each stop's situations for every screen (`v1:notices:entur:<stop id>`); this
    makes the notices from them each time the board asks, so their language and which have ended are as of then.
    """

    name = "Entur disruptions"

    def __init__(self, store: redis.Redis, stop_ids: list[str]) -> None:
        """Read from `store`; `stop_ids` are the configured stop places or quays, as `v1:sub:entur` has them."""
        # Never asks Entur itself, so it has no session (NoticeSource.__init__).
        self.store = store
        self.stop_ids = list(stop_ids)

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Make the notices from what the store has for the stops, leaving out (and logging) any it has nothing for.

        Raises NoStoredSituationsError if it has nothing for any of them, so the board leaves it out; a RedisError if
        the store can't be asked.
        """
        if not self.stop_ids:
            return []
        values = self.store.mget([keys.entur_situations(stop_id) for stop_id in self.stop_ids])
        missing = [stop_id for stop_id, value in zip(self.stop_ids, values, strict=True) if value is None]
        if len(missing) == len(self.stop_ids):
            msg = f"No situations in the store for Entur stops {', '.join(missing)}"
            raise NoStoredSituationsError(msg)
        if missing:
            # Its provider hasn't got to it yet, or couldn't fetch it: the other stops' still count.
            logger.warning("No situations in the store for Entur stops %s", ", ".join(missing))
        return situation_notices([StoredSituations.model_validate_json(v) for v in values if v is not None], now)
