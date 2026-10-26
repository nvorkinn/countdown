"""gov.uk's bank holidays in the store: the value of `v1:notices:bank-holidays` (keys.bank_holidays).

gov.uk publishes one small file for all of the UK (https://www.gov.uk/bank-holidays.json), every division's holidays
from a few years back to a year or so ahead. The bank holidays provider (countdown-providers) downloads it a few times a
day for every screen at once, with no subscription, and writes it here; the notice board
(countdown_shared.notices.calendar.StoredBankHolidaySource) picks the screen's division and words its notice when it
draws. The value is gov.uk's answer as these models read it (its top-level divisions under `divisions`), beside
`fetched_at`:

    {"fetched_at": "2026-10-18T06:00:00Z", "divisions": {"england-and-wales": {"events": [{"title": "Christmas Day",
     "date": "2026-12-25", "notes": ""}, ...]}, "scotland": {...}, "northern-ireland": {...}}}

tests/fixtures/bank_holidays.json is an answer in gov.uk's own shape, which the provider's and the board's tests both
read.
"""

from datetime import date

from pydantic import BaseModel

from countdown_shared.store.models import Stored

# gov.uk's divisions, as its file names them. England and Wales share one.
DIVISIONS = ("england-and-wales", "scotland", "northern-ireland")


class BankHoliday(BaseModel):
    """One of gov.uk's bank holidays."""

    title: str
    date: date
    notes: str = ""  # e.g. "Substitute day"


class Division(BaseModel):
    """One part of the UK's bank holidays: England and Wales, Scotland or Northern Ireland."""

    events: list[BankHoliday]


class StoredBankHolidays(Stored):
    """The value of `v1:notices:bank-holidays` (TTL a week): gov.uk's bank holidays, every division's."""

    # By gov.uk's name for each, one of DIVISIONS.
    divisions: dict[str, Division]
