"""The developer panel's notices, from what the developer provider stored.

Their wording is countdown_shared's (notices/wording/developer.py).
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource
from countdown_shared.notices.wording.developer import developer_notices
from countdown_shared.store import developer

if TYPE_CHECKING:
    from datetime import datetime

    import redis


class DeveloperNoticeSource(NoticeSource):
    """A failing build, a release failed or waiting for approval, a plan renewing soon or nearly used up.

    It reads `v1:developer:<device id>` itself, each time the board asks, as the developer panel does: so it says what
    the store has now, and nothing once the key has expired.
    """

    name = "Developer"

    def __init__(self, store: redis.Redis, device_id: str) -> None:
        """Read the device `device_id`'s developer data from `store`."""
        # Never asks GitHub itself (the provider does), so it has no session (NoticeSource.__init__).
        self.store = store
        self.device_id = device_id

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Make the notices from what's stored: NoStoredDeveloperError if there's none, RedisError if it can't ask."""
        return developer_notices(developer.read(self.store, self.device_id), now)
