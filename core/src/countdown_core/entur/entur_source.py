"""Entur as a departures source: Norway's stops and their departures, looked up for the Entur provider."""

from typing import TYPE_CHECKING

from countdown_core.entur.entur_client import EnturClient, EnturDeparture, EnturStop

if TYPE_CHECKING:
    from datetime import datetime

    import requests

# Enough to fill the tallest stop: a big departure and a list under it, or a row each.
DEPARTURES_PER_STOP = 12


class EnturSource:
    """Norway's stops for the Entur provider: stop places and quays, by NSR id."""

    def __init__(self, session: requests.Session) -> None:
        """Ask Entur over `session`, which the caller owns."""
        self.client = EnturClient(session)

    def resolve(self, stop_id: str) -> EnturStop | None:
        """Look up a stop place or quay by NSR id; None if Entur doesn't know it."""
        return self.client.get_stop_info(stop_id)

    def departures(self, stop: EnturStop, now: datetime | None = None) -> list[EnturDeparture]:
        """Fetch the stop's next departures as of `now` (default: now), soonest first; raises if Entur fails."""
        return self.client.get_departures(stop.id, limit=DEPARTURES_PER_STOP, now=now)
