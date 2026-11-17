"""What a notice is: one line of the notice board, with how much it matters and how long it holds.

Here rather than with the board that draws it (countdown_renderer.notices), so whatever words notices can make them
without the renderer.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class Severity(IntEnum):
    """Lower sorts first on the notice board."""

    SEVERE = 0  # red weather warning, severe flood warning, line suspended
    WARNING = 1  # amber weather warning, flood warning, severe delays, part suspended
    INFO = 2  # yellow weather warning, flood alert, issues reported, station notices
    REMINDER = 3  # coming up soon: a bank holiday, the clocks changing
    # Planned engineering works: shown only in rows the others leave free, and never
    # counted in the board's "+N more".
    PLANNED = 4


@dataclass(frozen=True)
class Notice:
    """One line on the notice board."""

    source: str  # shown as a tag in front of the text: "Met Office", "TfL", ...
    severity: Severity
    text: str  # one line; the panel truncates whatever doesn't fit
    valid_until: datetime | None = None  # dropped from the board after this, if known
    display_time: datetime | None = None  # a generated clock appended to text at render time, not necessarily expiry
