"""The disruptions at an Entur stop in the store: the value of `v1:notices:entur:<stop id>`.

Written by provider-entur-situations: Entur's own answer to the situations query (countdown_core.notices.entur's
SITUATIONS_QUERY, or QUAY_SITUATIONS_QUERY for a quay), its GraphQL `data` exactly as it came, so the renderer makes
the notices from it as it draws (StoredEnturSituationsSource): each summary in the screen's language, "Line 5"
translated, and the situations that have ended since left out.

    {"fetched_at": "2026-10-16T12:00:00+00:00", "stop_id": "NSR:StopPlace:58381",
     "data": {"stopPlace": {"name": "Majorstuen", "situations": [<situation>, ...],
                            "estimatedCalls": [{"serviceJourney": {"line": {"publicCode": "5"}},
                                                "situations": [<situation>, ...]}, ...]}}}

where a situation is {"summary": [{"value": "Buss for T-bane", "language": "no"}, ...], "validityPeriod":
{"endTime": "2026-10-20T00:00:00+02:00"}, "severity": "severe"}. A quay's answer is under "quay" instead. A stop Entur
doesn't know is written too, as Entur answers it ({"stopPlace": null}), so it isn't mistaken for a missing key.
"""

from typing import Any

from countdown_core.store.models import Stored


class StoredSituations(Stored):
    """Entur's answer for one stop place or quay: its situations, and those of the next lines leaving it."""

    # The id it was fetched for, as `v1:sub:entur` has it, e.g. "NSR:StopPlace:58381" or "NSR:Quay:7333".
    stop_id: str
    # Entur's GraphQL `data`, as it came.
    data: dict[str, Any]

    def stop(self) -> dict[str, Any] | None:
        """Return the stop place or quay Entur answered with, or None if Entur doesn't know the stop."""
        stop = self.data.get("quay" if self.stop_id.startswith("NSR:Quay:") else "stopPlace")
        return stop if isinstance(stop, dict) else None
