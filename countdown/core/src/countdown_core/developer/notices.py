"""The developer panel's notices, from what its client last fetched."""

from datetime import timedelta
from typing import TYPE_CHECKING, override

from countdown_core.i18n import _, local_zone
from countdown_core.notices.notice import Notice, NoticeSource, Severity

if TYPE_CHECKING:
    from datetime import datetime

    import requests

    from countdown_core.developer.developer_client import DeveloperClient

# A plan this far used up gets a notice.
NEARLY_USED_UP = 0.9


class DeveloperNoticeSource(NoticeSource):
    """A build failing on a default branch, a plan renewing today or tomorrow, a plan nearly used up.

    It asks nothing itself: it reads the client's last answer, so it has nothing until the client has fetched.
    """

    name = "Developer"
    refresh_interval = timedelta(minutes=1)
    needs_network = False

    def __init__(self, session: requests.Session, client: DeveloperClient) -> None:
        """Read `client`'s last answer (`session` is unused: the client does the fetching)."""
        super().__init__(session)
        self.client = client

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        summary = self.client.summary
        if summary is None:
            return []
        notices = []
        if summary.github is not None:
            notices.extend(
                Notice("GitHub", Severity.WARNING, _("{repo}: build failing").format(repo=repo))
                for repo in summary.github.failing
            )
        today = now.astimezone(local_zone()).date()
        for plan in summary.plans:
            days = (plan.renews - today).days
            if days == 0:
                notices.append(Notice(plan.name, Severity.REMINDER, _("{plan} renews today").format(plan=plan.plan)))
            elif days == 1:
                notices.append(Notice(plan.name, Severity.REMINDER, _("{plan} renews tomorrow").format(plan=plan.plan)))
            share = plan.share_used
            if share is not None and share >= NEARLY_USED_UP:
                text = _("{percent}% of {plan} used").format(percent=round(100 * share), plan=plan.plan)
                notices.append(Notice(plan.name, Severity.INFO, text))
        return notices
