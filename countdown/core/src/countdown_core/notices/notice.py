"""What the notice board shows (Notices) and where it gets them from (NoticeSources)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    import requests


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


class NoticeSource(ABC):
    """One feed the notice board polls.

    Each source decides for itself what's worth reporting and returns it as Notices; the board handles when to call
    it, what to do when it fails, and how everything is ranked and drawn.
    """

    name: str
    refresh_interval: timedelta
    # False for a source that works everything out locally (the clocks changing), so the
    # board doesn't count its answers as proof that anything could be reached.
    needs_network = True

    def __init__(self, session: requests.Session) -> None:
        """Fetch over `session`, the notice board's retrying one."""
        self.session = session

    @abstractmethod
    def fetch(self, now: datetime) -> list[Notice]:
        """Return everything this source currently has to say (empty if all is well).

        Raises if the feed can't be reached or read, so the board keeps the last good answer.
        """
