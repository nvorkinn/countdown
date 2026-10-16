from typing import TYPE_CHECKING

from countdown_core.entur.entur_client import EnturDeparture, EnturStop, TransportMode
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Icon, Style

if TYPE_CHECKING:
    from PIL import Image, ImageDraw

ICON_BY_MODE: dict[TransportMode, Icon] = {
    TransportMode.METRO: "subway",
    TransportMode.TRAM: "tram",
    TransportMode.RAIL: "train",
    TransportMode.WATER: "ferry",
}


class EnturArrivalPanel(AbstractArrivalPanel):
    """A Norwegian stop's departures, drawn like a London bus stop's: a row per line and destination, and the
    platform's code where a London stop has its letter.
    """

    def __init__(self, stop: EnturStop, departures: list[EnturDeparture], style: Style = "classic"):
        super().__init__(departures, style)
        self.stop = stop

    @property
    def stop_name(self) -> str:
        return self.stop.name

    @property
    def stop_letter(self) -> str | None:
        return self.stop.public_code

    @property
    def icon(self) -> Icon:
        # A station served by several modes (Majorstuen: metro and tram) shows the first, which Entur gives as
        # its main one.
        modes = self.stop.transport_modes
        return ICON_BY_MODE.get(modes[0], "bus") if modes else "bus"

    @staticmethod
    def _route_key(departure: EnturDeparture) -> tuple[str, str]:
        return departure.line, departure.destination

    @staticmethod
    def _badge(departure: EnturDeparture) -> str:
        return departure.line

    @staticmethod
    def _destination(departure: EnturDeparture) -> str:
        return departure.destination

    def add_header(self, img: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        draw.text((7, 10), self.stop_name, "white", font_size=15, anchor="lm")
        if self.stop_letter:
            draw.text((img.size[0] - 9, 10), self.stop_letter, "white", anchor="rm", stroke_width=0.2)
