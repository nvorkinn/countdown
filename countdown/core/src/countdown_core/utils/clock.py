from datetime import datetime, time
from typing import Literal

type ClockFormat = Literal["12h", "24h"]


def format_clock(value: datetime | time, clock_format: ClockFormat = "24h") -> str:
    """A user-facing clock, in the value's own time zone, without seconds."""
    match clock_format:
        case "24h":
            return f"{value.hour:02d}:{value.minute:02d}"
        case "12h":
            period = "AM" if value.hour < 12 else "PM"
            return f"{value.hour % 12 or 12}:{value.minute:02d} {period}"
        case _:
            raise ValueError(f"Unknown clock format: {clock_format}")
