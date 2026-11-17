"""The wording of the notices worked out from the calendar: the UK's bank holidays and the clocks changing.

The bank holidays are gov.uk's, as the bank holidays provider stored them (store/bank_holidays.py); picking the
division is the caller's. The clocks changing is worked out here, from the time zone alone.
"""

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from countdown_shared.i18n import N_, _, format_date
from countdown_shared.notices.wording.notice import Notice, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from countdown_shared.store.bank_holidays import BankHoliday

UK_TIME = ZoneInfo("Europe/London")

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


def clock_change_notices(now: datetime, zone: ZoneInfo) -> list[Notice]:
    """Remind of the clocks in `zone` going forward or back, in the week before they do; nothing otherwise."""
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
