from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.i18n import N_, _
from countdown_core.notices.models import FloodWarningsResponse
from countdown_core.notices.notice import Notice, NoticeSource, Severity

if TYPE_CHECKING:
    from countdown_core.tfl.models import Postcode

FLOODS_URL = "https://environment.data.gov.uk/flood-monitoring/id/floods"
RADIUS_KM = 5

# severityLevel 4 is "warning no longer in force", which isn't worth showing.
SEVERITY_BY_LEVEL = {
    1: (Severity.SEVERE, N_("Severe flood warning")),
    2: (Severity.WARNING, N_("Flood warning")),
    3: (Severity.INFO, N_("Flood alert")),
}


class FloodWarningsSource(NoticeSource):
    """Environment Agency flood alerts and warnings near the postcode. England only:
    elsewhere the feed just has nothing to report.
    """

    name = "Flood warnings"
    refresh_interval = timedelta(minutes=15)

    def __init__(self, session, location: Postcode):
        super().__init__(session)
        self.params = {"lat": location.latitude, "long": location.longitude, "dist": RADIUS_KM}

    def fetch(self, now: datetime) -> list[Notice]:
        response = self.session.get(FLOODS_URL, params=self.params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        notices = []
        for warning in FloodWarningsResponse.model_validate_json(response.content).items:
            if warning.severity_level in SEVERITY_BY_LEVEL:
                severity, label = SEVERITY_BY_LEVEL[warning.severity_level]
                notices.append(Notice(_("Floods"), severity, f"{_(label)}: {warning.description}"))
        return notices
