"""Where the notice board gets its notices from (NoticeSources).

What a Notice is, and its Severity, are countdown_shared's (notices/wording/notice.py), so a provider can make them
too; both are still importable from here.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from countdown_shared.notices.wording.notice import Notice, Severity

if TYPE_CHECKING:
    from datetime import datetime

    import requests

__all__ = ["Notice", "NoticeSource", "Severity"]


class NoticeSource(ABC):
    """One feed the notice board polls.

    Each source decides for itself what's worth reporting and returns it as Notices; the board asks every source on
    every poll, and handles what to do when one fails and how everything is ranked and drawn.
    """

    name: str
    # False for a source that works everything out locally (the clocks changing), so the
    # board doesn't count its answers as proof that anything could be reached.
    needs_network = True

    def __init__(self, session: requests.Session) -> None:
        """Fetch over `session`, the notice board's retrying one."""
        self.session = session

    @abstractmethod
    def fetch(self, now: datetime) -> list[Notice]:
        """Return everything this source currently has to say (empty if all is well).

        Raises if it can't say: a NoStoredDataError if its key in the store has expired (or isn't there yet), so the
        board leaves it out, or a RedisError if the store can't be asked, so the board keeps what it last showed.
        """
