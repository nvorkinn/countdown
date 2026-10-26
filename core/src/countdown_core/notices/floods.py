"""Flood warnings near the screen: the Environment Agency's, from the store (written by the floods provider).

The renderer never asks the EA itself; the provider fetches each postcode's warnings once for every screen there.
"""

from typing import TYPE_CHECKING, override

from countdown_core.i18n import N_, _
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.store import keys, reader
from countdown_core.store.floods import StoredFloods

if TYPE_CHECKING:
    from datetime import datetime

    import requests

    from countdown_core.notices.models import FloodWarningsResponse

# severityLevel 4 is "warning no longer in force", which isn't worth showing.
SEVERITY_BY_LEVEL = {
    1: (Severity.SEVERE, N_("Severe flood warning")),
    2: (Severity.WARNING, N_("Flood warning")),
    3: (Severity.INFO, N_("Flood alert")),
}


class NoStoredFloodsError(reader.NoStoredDataError):
    """The store has no flood warnings for the postcode: the floods provider is down, or hasn't fetched them yet."""


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


class StoredFloodWarningsSource(NoticeSource):
    """Environment Agency flood alerts and warnings near the postcode, from the store.

    That's `v1:notices:floods:<place>`, the EA's warnings for the flood areas within 5 km of the postcode, written by
    the floods provider; the notices are made from them as they're read. England only: elsewhere the EA just has
    nothing to report.
    """

    name = "Flood warnings"

    def __init__(self, session: requests.Session, postcode: str) -> None:
        """Read what the provider found near `postcode`, as the broker sends it (`session` is unused: no HTTP)."""
        super().__init__(session)
        self.key = keys.floods(postcode)

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Return the stored warnings' notices.

        Raises NoStoredFloodsError if the store has none, so the board leaves them out, and
        reader.StoreNotConfiguredError if the renderer has no store.
        """
        stored = reader.read(reader.require_store(), self.key, StoredFloods)
        if stored is None:
            # Not naming the postcode: the board logs this, and the postcode says where the screen is.
            msg = "No flood warnings in the store for the board's postcode"
            raise NoStoredFloodsError(msg)
        return flood_notices(stored.floods)
