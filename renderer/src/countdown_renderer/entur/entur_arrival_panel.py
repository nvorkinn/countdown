"""The departures panel for a Norwegian stop."""

from typing import TYPE_CHECKING, override

from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel, Icon
from countdown_shared.entur.entur_client import EnturDeparture, EnturStop, TransportMode

if TYPE_CHECKING:
    from countdown_renderer.style import Style

ICON_BY_MODE: dict[TransportMode, Icon] = {
    TransportMode.METRO: "subway",
    TransportMode.TRAM: "tram",
    TransportMode.RAIL: "train",
    TransportMode.WATER: "ferry",
}


class EnturArrivalPanel(AbstractArrivalPanel[EnturDeparture]):
    """A Norwegian stop's departures, drawn like a London bus stop's.

    A row per line and destination, and the platform's code where a London stop has its letter.
    """

    def __init__(self, stop: EnturStop, departures: list[EnturDeparture], style: Style = "hero") -> None:
        """Draw `departures` from `stop` in the given departures style."""
        super().__init__(departures, style)
        self.stop = stop
        self._arrivals = departures

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
        return self.icons[0]

    @property
    @override
    def icons(self) -> list[Icon]:
        # Every mode the stop serves (Majorstuen: metro and tram), in the order Entur gives them (its main one
        # first), then any a departure has that the stop didn't list. Modes drawn alike (bus, coach) count once.
        modes = [*self.stop.transport_modes, *(departure.mode for departure in self._arrivals)]
        icons = list(dict.fromkeys(ICON_BY_MODE.get(mode, "bus") for mode in modes))
        return icons or ["bus"]

    @staticmethod
    @override
    def _icon(arrival: EnturDeparture) -> Icon:
        return ICON_BY_MODE.get(arrival.mode, "bus")

    @staticmethod
    @override
    def _route_key(arrival: EnturDeparture) -> tuple[str, str]:
        return arrival.line, arrival.destination

    @staticmethod
    @override
    def _badge(arrival: EnturDeparture) -> str:
        return arrival.line

    @staticmethod
    @override
    def _destination(arrival: EnturDeparture) -> str:
        return arrival.destination
