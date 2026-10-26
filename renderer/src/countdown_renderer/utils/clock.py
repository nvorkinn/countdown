"""Clock times as the screen shows them, in the 12- or 24-hour format."""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime, time

type ClockFormat = Literal["12h", "24h"]

# Hours in each of the 12-hour clock's halves of the day (AM and PM).
HALF_DAY_HOURS = 12


def format_clock(value: datetime | time, clock_format: ClockFormat = "24h") -> str:
    """Format `value` as a user-facing clock, in the value's own time zone, without seconds."""
    match clock_format:
        case "24h":
            return f"{value.hour:02d}:{value.minute:02d}"
        case "12h":
            period = "AM" if value.hour < HALF_DAY_HOURS else "PM"
            return f"{value.hour % HALF_DAY_HOURS or HALF_DAY_HOURS}:{value.minute:02d} {period}"
        case _:
            msg = f"Unknown clock format: {clock_format}"
            raise ValueError(msg)
