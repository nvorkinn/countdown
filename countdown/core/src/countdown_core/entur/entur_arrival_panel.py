"""The departures panel for a Norwegian stop."""

from typing import override

from countdown_core.entur.entur_client import EnturDeparture, EnturStop, TransportMode
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Icon, Style

ICON_BY_MODE: dict[TransportMode, Icon] = {
    TransportMode.METRO: "subway",
    TransportMode.TRAM: "tram",
    TransportMode.RAIL: "train",
    TransportMode.WATER: "ferry",
}


class EnturArrivalPanel(AbstractArrivalPanel):
    """A Norwegian stop's departures, drawn like a London bus stop's.

    A row per line and destination, and the platform's code where a London stop has its letter.
    """

    def __init__(self, stop: EnturStop, departures: list[EnturDeparture], style: Style = "hero") -> None:
        """Draw `departures` from `stop` in the given departures style."""
        super().__init__(departures, style)
        self.stop = stop

    @property
    @override
    def stop_name(self) -> str:
        return self.stop.name

    @property
    @override
    def stop_letter(self) -> str | None:
        return self.stop.public_code

    @property
    @override
    def icon(self) -> Icon:
        # A station served by several modes (Majorstuen: metro and tram) shows the first, which Entur gives as
        # its main one.
        modes = self.stop.transport_modes
        return ICON_BY_MODE.get(modes[0], "bus") if modes else "bus"

    @staticmethod
    @override
    def _route_key(departure: EnturDeparture) -> tuple[str, str]:
        return departure.line, departure.destination

    @staticmethod
    @override
    def _badge(departure: EnturDeparture) -> str:
        return departure.line

    @staticmethod
    @override
    def _destination(departure: EnturDeparture) -> str:
        return departure.destination
