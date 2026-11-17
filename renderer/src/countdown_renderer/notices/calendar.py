"""Notices worked out from the calendar: the UK's bank holidays and the clocks changing.

The bank holidays are gov.uk's, which the bank holidays provider (countdown-providers) writes to the store for all of
the UK (`v1:notices:bank-holidays`); the board reads them from there and picks its division. The clocks changing is
worked out with no network at all. The wording of both is countdown_shared's (notices/wording/calendar.py).
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource
from countdown_shared.i18n import local_zone
from countdown_shared.notices.wording.calendar import bank_holiday_notices, clock_change_notices
from countdown_shared.store import keys, reader
from countdown_shared.store.bank_holidays import StoredBankHolidays

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "ClockChangeSource",
    "NoStoredBankHolidaysError",
    "StoredBankHolidaySource",
    "bank_holiday_notices",
]

# gov.uk's lists, keyed by postcodes.io's country. England and Wales share one.
DIVISION_BY_COUNTRY = {"Scotland": "scotland", "Northern Ireland": "northern-ireland"}
DEFAULT_DIVISION = "england-and-wales"


class NoStoredBankHolidaysError(reader.NoStoredDataError):
    """The store has no bank holidays for the division: the bank holidays provider is down, or hasn't fetched them."""


class StoredBankHolidaySource(NoticeSource):
    """The next bank holiday for the device's part of the UK, once it's less than a month away, from the store.

    England and Wales unless the postcode says otherwise. gov.uk's list is in the store (keys.BANK_HOLIDAYS, written by
    the bank holidays provider for all of the UK); each poll reads it and works the notice out afresh, so "Tomorrow",
    the date and the translations are as of drawing.
    """

    name = "Bank holidays"

    def __init__(self, country: str | None = None) -> None:
        """Pick `country`'s division (postcodes.io's country; None for England and Wales)."""
        # No session (NoticeSource's): it never calls gov.uk, the provider does.
        self.division = DIVISION_BY_COUNTRY.get(country or "", DEFAULT_DIVISION)

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Return the next bank holiday's notice, if it's less than a month away.

        Raises NoStoredBankHolidaysError if the store has none for the division (so the board leaves it out), and
        StoreNotConfiguredError without a store. The store is asked for here, not when the board is built, so a
        renderer without one still has its board.
        """
        stored = reader.read(reader.require_store(), keys.BANK_HOLIDAYS, StoredBankHolidays)
        division = stored.divisions.get(self.division) if stored is not None else None
        if division is None:
            msg = f"The store has no bank holidays for {self.division} (is provider-bank-holidays running?)"
            raise NoStoredBankHolidaysError(msg)
        return bank_holiday_notices(division.events, now)


class ClockChangeSource(NoticeSource):
    """A reminder in the week before the clocks go forward or back, in the screen's time zone. Needs no network."""

    name = "Clock change"
    needs_network = False

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        return clock_change_notices(now, local_zone())
