"""The bins provider: each household's bin collection days, from Binable, written to `v1:bins:<SHA1 of the address>`.

It reads the broker's addresses (`v1:sub:address`, "<street>|<house_number>|<postcode>|<city>", one per Norwegian notice
board with an address set) and asks Binable (https://binable.app, POST /api/fetch) for each one's collection calendar,
which Binable has from the municipality (Oslo kommune's, say) about three months ahead. A calendar changes a few times a
year, so each address is asked about twice a day and kept for a day and a half: Binable being down for a day changes
nothing on the screens.

Binable answers in one of two shapes: each kind of waste at the top level, beside `address` and `provider`, or under
`collections` with a `typeMap` of its German labels. Both are read, and only the kinds of waste with a day ahead kept.
An address Binable has no provider for (400) or no calendar for (404) is written as not found, so a board shows no bin
notice rather than wait for one. Its rate limits are tight (an anonymous client is cut off after a handful of
requests), so it's asked with the BINABLE_API_KEY, one address after another, and after a 429 nothing more is asked
until the next cycle: those addresses keep what was stored.

Addresses are where people live: they're never logged, only counted.

The HTTP calls are synchronous (requests), so they run in a worker thread (asyncio.to_thread), like the other
providers'.
"""

import asyncio
import logging
import os
from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from countdown_providers.config import Settings
from countdown_providers.runner import serve
from countdown_shared.core.http_session import DEFAULT_TIMEOUT, retrying_session
from countdown_shared.core.log_format import configure_logging
from countdown_shared.store import keys
from countdown_shared.store.bins import BinCollection, StoredBinCollections

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

logger = logging.getLogger(__name__)

BINABLE_URL = "https://binable.app/api/fetch"
# Binable's answers for an address it can't give days for: no provider for it (400), or one without its calendar (404).
NOT_FOUND = (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)
# The members of an answer that aren't kinds of waste.
NOT_FRACTIONS = frozenset({"address", "provider", "empty", "typeMap", "collections", "error"})


class RateLimitedError(Exception):
    """Binable said this client has asked too often (a 429)."""


def parse_member(member: str) -> dict[str, str] | None:
    """Return Binable's address fields for a v1:sub:address `member`, or None if it isn't one."""
    parts = member.split("|")
    if len(parts) != 4 or not all(part.strip() for part in parts):  # noqa: PLR2004 -- street, number, postcode, city
        return None
    street, house_number, postcode, city = (part.strip() for part in parts)
    return {"street": street, "houseNumber": house_number, "zip": postcode, "city": city, "country": "NO"}


def collections(answer: dict[str, Any], today: date) -> list[BinCollection]:
    """Return each kind of waste in Binable's `answer` with a day from `today` on, its days ahead earliest first."""
    nested = answer.get("collections")
    fractions = nested if isinstance(nested, dict) else {k: v for k, v in answer.items() if k not in NOT_FRACTIONS}
    found = []
    for fraction, value in fractions.items():
        if not isinstance(value, dict):
            continue
        days = sorted({day for day in (_day(text) for text in value.get("dates") or []) if day and day >= today})
        if days:
            found.append(BinCollection(fraction=fraction, dates=days))
    return found


def _day(text: object) -> date | None:
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        return None


class BinsProvider:
    """Each Norwegian household's bin collection days, from Binable (with an API key)."""

    name = keys.ADDRESS
    # A day and a half: a calendar runs months ahead, so a missed cycle or two show the same notice.
    ttl_s = keys.BINS_TTL_S
    # Twice a day, for calendars that change a few times a year.
    interval_s = 12 * 60 * 60
    # Few at a time, one after another: Binable's limits are tight.
    batch_size = 5

    def __init__(
        self,
        session: requests.Session,
        api_key: str | None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Ask Binable through `session` with `api_key`, if any; `now` stamps fetched_at (for the tests)."""
        self.session = session
        self.headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}
        self.now = now

    def output_key(self, item_id: str) -> str:
        """Write to `v1:bins:<SHA1 of the address>`."""
        return keys.bins(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredBinCollections]:
        """Fetch each address's collection days, leaving out (and logging, by count) any whose request fails."""
        # One thread for the whole batch, one address after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredBinCollections]:
        results: dict[str, StoredBinCollections] = {}
        failed = 0
        for asked, member in enumerate(ids):
            address = parse_member(member)
            if address is None:
                failed += 1
                continue
            try:
                results[member] = self._fetch_one(address)
            except RateLimitedError:
                logger.warning(
                    "Binable's rate limit is reached; %d addresses wait for the next cycle", len(ids) - asked
                )
                break
            except Exception as e:  # noqa: BLE001 -- one bad address mustn't lose the rest of the batch
                # Not the exception's text: Binable's errors name the address.
                logger.warning("Couldn't fetch an address's bin collection days from Binable: %s", type(e).__name__)
                failed += 1
        if failed:
            logger.warning("%d of %d addresses' bin collection days couldn't be fetched", failed, len(ids))
        logger.debug("Fetched %d addresses' bin collection days", len(results))
        return results

    def _fetch_one(self, address: dict[str, str]) -> StoredBinCollections:
        fetched_at = self.now()
        response = self.session.post(BINABLE_URL, json=address, headers=self.headers, timeout=DEFAULT_TIMEOUT)
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimitedError
        if response.status_code in NOT_FOUND:
            return StoredBinCollections(fetched_at=fetched_at, found=False, collections=[])
        response.raise_for_status()
        answer = response.json()
        found = collections(answer, fetched_at.date())
        provider = answer.get("provider")
        return StoredBinCollections(
            fetched_at=fetched_at,
            found=bool(found),
            provider=provider.get("name") if isinstance(provider, dict) else None,
            collections=found,
        )


def main() -> None:
    """Run the `countdown-provider-bins` console script, with Binable's key from BINABLE_API_KEY."""
    configure_logging()
    api_key = os.environ.get("BINABLE_API_KEY") or None
    if api_key is None:
        logger.warning("No BINABLE_API_KEY: asking Binable anonymously, at its tighter rate limits")
    # countdown_shared's retrying session: retries connection errors and 502/503/504 with backoff (never a 429).
    session = retrying_session()
    asyncio.run(serve(BinsProvider(session, api_key), Settings.from_env()))


if __name__ == "__main__":
    main()
