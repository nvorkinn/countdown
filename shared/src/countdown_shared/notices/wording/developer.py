"""The wording of the developer panel's notices, from what the developer provider stored (store/developer.py).

In the screen's language, with "today" the screen's. Reading the store is the caller's.
"""

from typing import TYPE_CHECKING

from countdown_shared.i18n import _, local_zone
from countdown_shared.notices.wording.notice import Notice, Severity

if TYPE_CHECKING:
    from datetime import datetime

    from countdown_shared.developer.models import DeveloperSummary

# A plan this far used up gets a notice.
NEARLY_USED_UP = 0.9


def developer_notices(summary: DeveloperSummary, now: datetime) -> list[Notice]:
    """Make the notices from `summary`, as of `now`.

    A failing build, a release failed or waiting for approval, a plan renewing soon or nearly used up.
    """
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
