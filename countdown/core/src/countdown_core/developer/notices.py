"""The developer panel's notices, from what the developer provider stored."""

from typing import TYPE_CHECKING, override

from countdown_core.i18n import _, local_zone
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.store import developer

if TYPE_CHECKING:
    from datetime import datetime

    import redis

# A plan this far used up gets a notice.
NEARLY_USED_UP = 0.9


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
        summary = developer.read(self.store, self.device_id)
        notices = []
        if summary.github is not None:
            notices.extend(
                Notice("GitHub", Severity.WARNING, _("{repo}: build failing").format(repo=repo))
                for repo in summary.github.failing
            )
        for release in summary.releases:
            name = f"{release.repo} {release.title}"
            if release.state == "failed":
                notices.append(Notice("GitHub", Severity.WARNING, _("{release}: release failed").format(release=name)))
            notices.extend(
                Notice(
                    "GitHub",
                    Severity.REMINDER,
                    _("{release}: {job} waiting for your approval").format(release=name, job=job),
                )
                for job in release.waiting
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
