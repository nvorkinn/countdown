from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.notices.notice import Notice, NoticeSource, Severity

UK_TIME = ZoneInfo("Europe/London")

BANK_HOLIDAYS_URL = "https://www.gov.uk/bank-holidays.json"
# gov.uk's lists, keyed by postcodes.io's country. England and Wales share one.
DIVISION_BY_COUNTRY = {"Scotland": "scotland", "Northern Ireland": "northern-ireland"}
DEFAULT_DIVISION = "england-and-wales"
# How often the list itself is downloaded; the notices are worked out from it every refresh.
BANK_HOLIDAYS_REDOWNLOAD = timedelta(days=1)

# How far ahead the clocks changing is mentioned.
CLOCK_CHANGE_NOTICE = timedelta(days=7)


class BankHoliday(BaseModel):
    title: str
    date: date
    notes: str = ""  # e.g. "Substitute day"


class Division(BaseModel):
    events: list[BankHoliday]


class BankHolidays(BaseModel):
    divisions: dict[str, Division] = Field(default_factory=dict)


def _one_month_after(day: date) -> date:
    """The same day next month, or that month's last day if it's shorter."""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise AssertionError("every month has a 28th")


def _when(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return f"{day:%a} {day.day} {day:%b}"


class BankHolidaySource(NoticeSource):
    """The next bank holiday for the device's part of the UK, once it's less than a month
    away. England and Wales unless the postcode says otherwise."""

    name = "Bank holidays"
    refresh_interval = timedelta(hours=1)  # so "Tomorrow" becomes "Today" on time

    def __init__(self, session, country: str | None = None):
        super().__init__(session)
        self.division = DIVISION_BY_COUNTRY.get(country or "", DEFAULT_DIVISION)
        self.events: list[BankHoliday] | None = None
        self.downloaded: datetime | None = None

    def _download(self, now: datetime) -> list[BankHoliday]:
        if self.events is None or self.downloaded is None or now - self.downloaded >= BANK_HOLIDAYS_REDOWNLOAD:
            response = self.session.get(BANK_HOLIDAYS_URL, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            # The divisions are the top-level keys; wrap them so pydantic can read them.
            divisions = BankHolidays.model_validate({"divisions": response.json()}).divisions
            self.events = divisions[self.division].events
            self.downloaded = now
        return self.events

    def fetch(self, now: datetime) -> list[Notice]:
        today = now.astimezone(UK_TIME).date()
        upcoming = [e for e in self._download(now) if today <= e.date < _one_month_after(today)]
        if not upcoming:
            return []
        holiday = min(upcoming, key=lambda e: e.date)
        text = f"{_when(holiday.date, today)}: {holiday.title}"
        if holiday.notes:
            text += f" ({holiday.notes.lower()})"
        ends = datetime.combine(holiday.date + timedelta(days=1), time(), tzinfo=UK_TIME)
        return [Notice("Bank holiday", Severity.REMINDER, text, ends)]


def next_clock_change(now: datetime) -> datetime | None:
    """When the UK clocks next change within CLOCK_CHANGE_NOTICE, as a UTC instant (the
    changes always happen on the hour), or None if they don't."""
    hour = now.astimezone(ZoneInfo("UTC")).replace(minute=0, second=0, microsecond=0)
    offset = hour.astimezone(UK_TIME).utcoffset()
    for _ in range(int(CLOCK_CHANGE_NOTICE / timedelta(hours=1)) + 1):
        hour += timedelta(hours=1)
        if hour.astimezone(UK_TIME).utcoffset() != offset:
            return hour
    return None


class ClockChangeSource(NoticeSource):
    """A reminder in the week before the clocks go forward or back. Needs no network."""

    name = "Clock change"
    refresh_interval = timedelta(hours=1)
    needs_network = False

    def fetch(self, now: datetime) -> list[Notice]:
        change = next_clock_change(now)
        if change is None:
            return []
        before = (change - timedelta(microseconds=1)).astimezone(UK_TIME)
        after = change.astimezone(UK_TIME)
        direction = "forward" if after.utcoffset() > before.utcoffset() else "back"
        # Said in the time it is just before the change: 01:00 in March, 02:00 in October.
        at = change.astimezone(UK_TIME) - (after.utcoffset() - before.utcoffset())
        today = now.astimezone(UK_TIME).date()
        day = before.date()
        when = "tonight" if day in (today, today + timedelta(days=1)) else f"on {_when(day, today)}"
        return [
            Notice("Clocks", Severity.REMINDER, f"Clocks go {direction} an hour {when}, at", change, display_time=at)
        ]
