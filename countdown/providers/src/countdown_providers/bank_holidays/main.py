"""The bank holidays provider: gov.uk's bank holidays, for all of the UK at once, written to `v1:notices:bank-holidays`.

gov.uk publishes one small file (BANK_HOLIDAYS_URL) with every division's holidays, a year or so ahead, so there's
nothing to subscribe to: every UK notice board reads the same key and picks its own division, England and Wales,
Scotland or Northern Ireland (countdown_core.notices.calendar.StoredBankHolidaySource). The file changes a few times a
year, so it's fetched every 6 hours and kept for a week: gov.uk being down, even for days, changes nothing on the
screens.

Its one id is fixed (`fixed_ids`, see provider.Provider), so the runner fetches it whether or not anything is
subscribed. The HTTP call is synchronous (requests), so it runs in a worker thread (asyncio.to_thread), like the other
providers'.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.log_format import configure_logging
from countdown_core.store import keys
from countdown_core.store.bank_holidays import DIVISIONS, StoredBankHolidays
from countdown_providers.config import Settings
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

logger = logging.getLogger(__name__)

BANK_HOLIDAYS_URL = "https://www.gov.uk/bank-holidays.json"
# The one id it fetches: all of the UK, as gov.uk's one file has every division.
UK = "uk"


class BankHolidaysProvider:
    """gov.uk's bank holidays (keyless), every division's, for every screen."""

    # Not a subscription source (it has fixed_ids): only its name in the logs.
    name = "bank-holidays"
    fixed_ids = (UK,)
    # A week: the list runs a year or so ahead, so a copy days old shows the same notices as a new one.
    ttl_s = 7 * 24 * 60 * 60
    # A few times a day, for a file that changes a few times a year (next year's dates, a one-off holiday).
    interval_s = 6 * 60 * 60

    def __init__(self, session: requests.Session, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask gov.uk through `session`; `now` stamps fetched_at (a parameter for the tests)."""
        self.session = session
        self.now = now

    def output_key(self, item_id: str) -> str:  # noqa: ARG002 -- one key for its one id
        """Write to `v1:notices:bank-holidays`."""
        return keys.bank_holidays()

    async def fetch(self, ids: list[str]) -> dict[str, StoredBankHolidays]:
        """Fetch gov.uk's file, if `ids` has UK. Raises if gov.uk can't be reached or read, keeping the stored one."""
        if UK not in ids:
            return {}
        return {UK: await asyncio.to_thread(self._fetch_blocking)}

    def _fetch_blocking(self) -> StoredBankHolidays:
        fetched_at = self.now()
        response = self.session.get(BANK_HOLIDAYS_URL, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        # The divisions are the answer's top-level keys.
        stored = StoredBankHolidays(fetched_at=fetched_at, divisions=response.json())
        # An answer short of a division would leave its boards with nothing: keep the stored one instead.
        if missing := [division for division in DIVISIONS if division not in stored.divisions]:
            msg = f"gov.uk's bank holidays have no {', '.join(missing)}"
            raise ValueError(msg)
        logger.debug("Fetched gov.uk's bank holidays")
        return stored


def main() -> None:
    """Run the `countdown-provider-bank-holidays` console script. gov.uk needs no key."""
    configure_logging()
    # countdown_core's retrying session: retries connection errors and 502/503/504 with backoff.
    session = AbstractClient._build_retrying_session()  # noqa: SLF001 -- shared with countdown_core's clients on purpose
    asyncio.run(serve(BankHolidaysProvider(session), Settings.from_env()))


if __name__ == "__main__":
    main()
