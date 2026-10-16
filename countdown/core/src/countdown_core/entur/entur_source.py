"""Entur as a departures source: Norway's stops, looked up and drawn."""

from typing import TYPE_CHECKING

from countdown_core.entur.entur_arrival_panel import EnturArrivalPanel
from countdown_core.entur.entur_client import EnturClient, EnturStop

if TYPE_CHECKING:
    import requests

    from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Style

# Enough to fill the tallest stop: a hero's big one and a list under it, or a classic row each.
DEPARTURES_PER_STOP = 12


class EnturSource:
    """Norway's stops for the departures client: stop places and quays, by NSR id."""

    def __init__(self, session: requests.Session) -> None:
        """Ask Entur over `session`, which the caller owns."""
        self.client = EnturClient(session)

    def resolve(self, stop_id: str) -> EnturStop | None:
        """Look up a stop place or quay by NSR id; None if Entur doesn't know it."""
        return self.client.get_stop_info(stop_id)

    def panel(self, stop: EnturStop, style: Style) -> AbstractArrivalPanel:
        """Fetch the stop's next departures and draw them in `style`."""
        return EnturArrivalPanel(stop, self.client.get_departures(stop.id, limit=DEPARTURES_PER_STOP), style)
