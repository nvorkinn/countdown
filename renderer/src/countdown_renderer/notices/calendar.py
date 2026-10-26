"""Notices worked out from the calendar: the UK's bank holidays and the clocks changing.

The bank holidays are gov.uk's, which the bank holidays provider (countdown-providers) writes to the store for all of
the UK (`v1:notices:bank-holidays`); the board reads them from there and picks its division. The clocks changing is
worked out here, with no network at all.
"""

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, override
from zoneinfo import ZoneInfo

from countdown_core.i18n import N_, _, format_date, local_zone
from countdown_core.store import keys, reader
from countdown_core.store.bank_holidays import StoredBankHolidays
from countdown_renderer.notices.notice import Notice, NoticeSource, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from countdown_core.store.bank_holidays import BankHoliday

UK_TIME = ZoneInfo("Europe/London")

# gov.uk's lists, keyed by postcodes.io's country. England and Wales share one.
DIVISION_BY_COUNTRY = {"Scotland": "scotland", "Northern Ireland": "northern-ireland"}
DEFAULT_DIVISION = "england-and-wales"

# How far ahead the clocks changing is mentioned.
CLOCK_CHANGE_NOTICE = timedelta(days=7)

DECEMBER = 12


def _one_month_after(day: date) -> date:
    """Return the same day next month, or that month's last day if it's shorter."""
    year, month = (day.year + 1, 1) if day.month == DECEMBER else (day.year, day.month + 1)
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    msg = "every month has a 28th"
    raise AssertionError(msg)


def when(day: date, today: date) -> str:
    """Say `day` as "Today", "Tomorrow", or the date, e.g. "Fri 16 Oct", in the screen's language."""
    if day == today:
        return _("Today")
    if day == today + timedelta(days=1):
        return _("Tomorrow")
    # Translators: a CLDR date pattern (EEE weekday, d day, MMM month), e.g. "Fri 16 Oct". Norwegian: "EEE d. MMM".
    return format_date(day, _("EEE d MMM"))


class NoStoredBankHolidaysError(reader.NoStoredDataError):
    """The store has no bank holidays for the division: the bank holidays provider is down, or hasn't fetched them."""


def bank_holiday_notices(events: Iterable[BankHoliday], now: datetime) -> list[Notice]:
    """Say the first of `events` that's less than a month from `now`'s day in the UK, if there's one.

    In the screen's language, as of `now`: "Tomorrow: Christmas Day", "Today: Boxing Day (substitute day)", or the date.
    """
    today = now.astimezone(UK_TIME).date()
    upcoming = [e for e in events if today <= e.date < _one_month_after(today)]
    if not upcoming:
        return []
    holiday = min(upcoming, key=lambda e: e.date)
    text = f"{when(holiday.date, today)}: {holiday.title}"
    if holiday.notes:
        text += f" ({holiday.notes.lower()})"
    ends = datetime.combine(holiday.date + timedelta(days=1), time(), tzinfo=UK_TIME)
    return [Notice(_("Bank holiday"), Severity.REMINDER, text, ends)]


class StoredBankHolidaySource(NoticeSource):
    """The next bank holiday for the device's part of the UK, once it's less than a month away, from the store.

    England and Wales unless the postcode says otherwise. gov.uk's list is in the store (keys.bank_holidays, written by
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
        stored = reader.read(reader.require_store(), keys.bank_holidays(), StoredBankHolidays)
        division = stored.divisions.get(self.division) if stored is not None else None
        if division is None:
            msg = f"The store has no bank holidays for {self.division} (is provider-bank-holidays running?)"
            raise NoStoredBankHolidaysError(msg)
        return bank_holiday_notices(division.events, now)


def next_clock_change(now: datetime, zone: ZoneInfo) -> datetime | None:
    """Find when the clocks in `zone` next change within CLOCK_CHANGE_NOTICE, or None if they don't.

    The answer is a UTC instant: the changes always happen on the hour.
    """
    hour = now.astimezone(ZoneInfo("UTC")).replace(minute=0, second=0, microsecond=0)
    offset = hour.astimezone(zone).utcoffset()
    for _hour in range(int(CLOCK_CHANGE_NOTICE / timedelta(hours=1)) + 1):
        hour += timedelta(hours=1)
        if hour.astimezone(zone).utcoffset() != offset:
            return hour
    return None


# Separate texts for each direction, so a translation can word them differently.
CLOCKS_GO = {
    # Translators: {when} is "tonight" or "on Sun 25 Oct". The screen adds the time after it ("01:00" or "1:00 AM").
    "forward": N_("Clocks go forward an hour {when}, at"),
    "back": N_("Clocks go back an hour {when}, at"),
}


class ClockChangeSource(NoticeSource):
    """A reminder in the week before the clocks go forward or back, in the screen's time zone. Needs no network."""

    name = "Clock change"
    needs_network = False

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        zone = local_zone()
        change = next_clock_change(now, zone)
        if change is None:
            return []
        before = (change - timedelta(microseconds=1)).astimezone(zone)
        after = change.astimezone(zone)
        # Both aware (in `zone`), so neither offset is None.
        before_offset = before.utcoffset() or timedelta()
        after_offset = after.utcoffset() or timedelta()
        direction = "forward" if after_offset > before_offset else "back"
        # Said in the time it is just before the change: 01:00 in March, 02:00 in October in London. The board adds
        # the time, in the screen's clock format.
        at = change.astimezone(zone) - (after_offset - before_offset)
        today = now.astimezone(zone).date()
        day = before.date()
        on = _("tonight") if day in (today, today + timedelta(days=1)) else _("on {day}").format(day=when(day, today))
        text = _(CLOCKS_GO[direction]).format(when=on)
        return [Notice(_("Clocks"), Severity.REMINDER, text, change, display_time=at)]
