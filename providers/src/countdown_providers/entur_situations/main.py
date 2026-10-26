"""The Entur situations provider: `v1:sub:entur`'s NSR stop ids to `v1:notices:entur:<stop id>`, for the notice board.

Each stop's value is Entur's own answer to countdown_core.notices.entur_feed's situations query (with the same
ET-Client-Name header as the departures): the situations on the stop and on the next 20 lines leaving it. The render
pool makes the notices from it as it draws (StoredEnturSituationsSource).

Its own process rather than part of the Entur departures provider, though both read `v1:sub:entur`: situations change
far less often than departures, so this asks every five minutes where that asks every 30 seconds,
and a slow or failing situations query can't hold up the departures.

Entur needs no key. A client that names itself (ET-Client-Name) may make 1000 non-trip requests a minute, and 200 a
second at most:

    https://developer.entur.no/docs/open-services/journey-planner/rate-limiting

The departures provider sends the same name, so the two share that: a stop costs two requests a minute there, and a
fifth of one here. When Entur says the quota's used up (429), the stops still to ask wait for the next cycle rather
than count against it.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

from countdown_core.core.http_session import retrying_session
from countdown_core.core.log_format import configure_logging
from countdown_core.entur.entur_client import EnturClient
from countdown_core.notices.entur_feed import query_situations
from countdown_core.store import keys
from countdown_core.store.situations import StoredSituations
from countdown_providers.config import Settings
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Entur's quotas are per minute: once it says this client's is used up, nothing more is asked of it for this long.
QUOTA_BACKOFF = timedelta(minutes=1)


def _over_quota(error: Exception) -> bool:
    """Whether `error` is Entur saying the quota's used up: requests' HTTPError for a 429."""
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == HTTPStatus.TOO_MANY_REQUESTS


class EnturSituationsProvider:
    """The situations at Entur stops, and on the lines leaving them, as Entur answers for each."""

    name = keys.ENTUR
    # Three cycles: a missed one or two don't empty the board, and a provider that's down shows within a quarter hour.
    ttl_s = 15 * 60
    # A disruption is rarely news to the minute.
    interval_s = 5 * 60

    def __init__(self, client: EnturClient, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask Entur through `client`; `now` stamps fetched_at (a parameter for the tests)."""
        self.client = client
        self.now = now
        # Until when Entur's quota is used up, after a 429; None while it isn't.
        self.quota_wait_until: datetime | None = None

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:entur:<stop id>`."""
        return keys.entur_situations(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredSituations]:
        """Fetch each stop's situations, leaving out (and logging) any whose query fails.

        A stop Entur doesn't know is kept, as Entur answered it, so the renderer can tell it from a missing key.
        """
        # One thread for the whole batch, one stop after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredSituations]:
        results: dict[str, StoredSituations] = {}
        for asked, stop_id in enumerate(ids):
            fetched_at = self.now()
            if self.quota_wait_until is not None and fetched_at < self.quota_wait_until:
                logger.warning("Entur's quota is used up; %d stops wait for the next cycle", len(ids) - asked)
                break
            try:
                data = query_situations(self.client, stop_id)
            except Exception as e:  # noqa: BLE001 -- one bad stop mustn't lose the rest of the batch
                logger.warning("Couldn't fetch the situations at Entur stop %s: %s", stop_id, e)
                if _over_quota(e):
                    self.quota_wait_until = fetched_at + QUOTA_BACKOFF
                continue
            stored = StoredSituations(fetched_at=fetched_at, stop_id=stop_id, data=data)
            if stored.stop() is None:
                logger.warning("Entur doesn't know stop %s", stop_id)
            results[stop_id] = stored
        return results


def main() -> None:
    """Run the `countdown-provider-entur-situations` console script. Entur needs no key, only its ET-Client-Name."""
    configure_logging()
    settings = Settings.from_env()
    # countdown_core's retrying session: retries connection errors and 502/503/504 with backoff (never a 429).
    session = retrying_session()
    asyncio.run(serve(EnturSituationsProvider(EnturClient(session)), settings))


if __name__ == "__main__":
    main()
