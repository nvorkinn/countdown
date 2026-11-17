"""The wording of flood warnings: the Environment Agency's, as the floods provider stored them (store/floods.py).

In the screen's language. Reading the store is the caller's.
"""

from typing import TYPE_CHECKING

from countdown_shared.i18n import N_, _
from countdown_shared.notices.wording.notice import Notice, Severity

if TYPE_CHECKING:
    from countdown_shared.notices.models import FloodWarningsResponse

# severityLevel 4 is "warning no longer in force", which isn't worth showing.
SEVERITY_BY_LEVEL = {
    1: (Severity.SEVERE, N_("Severe flood warning")),
    2: (Severity.WARNING, N_("Flood warning")),
    3: (Severity.INFO, N_("Flood alert")),
}


def flood_notices(floods: FloodWarningsResponse) -> list[Notice]:
    """Return the board's notices for the EA's warnings, in the screen's language, leaving out those no longer in force.

    In the EA's order: the board ranks them.
    """
    notices = []
    for warning in floods.items:
        if warning.severity_level in SEVERITY_BY_LEVEL:
            severity, label = SEVERITY_BY_LEVEL[warning.severity_level]
            notices.append(Notice(_("Floods"), severity, f"{_(label)}: {warning.description}"))
    return notices
