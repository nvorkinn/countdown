import requests

from countdown_core.entur.entur_arrival_panel import EnturArrivalPanel
from countdown_core.entur.entur_client import EnturClient, EnturStop
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Style

# Enough to fill the tallest stop: a hero's big one and a list under it, or a classic row each.
DEPARTURES_PER_STOP = 12


class EnturSource:
    """Norway's stops for the departures client: stop places and quays, by NSR id."""

    def __init__(self, session: requests.Session):
        self.client = EnturClient(session)

    def resolve(self, stop_id: str) -> EnturStop | None:
        return self.client.get_stop_info(stop_id)

    def panel(self, stop: EnturStop, style: Style) -> AbstractArrivalPanel:
        return EnturArrivalPanel(stop, self.client.get_departures(stop.id, limit=DEPARTURES_PER_STOP), style)
