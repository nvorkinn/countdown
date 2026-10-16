"""A TfL bus (or tram) stop's departures, a row per route."""

from typing import TYPE_CHECKING, override

from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Style

if TYPE_CHECKING:
    from PIL import Image, ImageDraw

    from countdown_core.tfl.models import BusArrival, SingleStopPoint


class BusArrivalPanel(AbstractArrivalPanel):
    """A bus stop's departures, headed by its name and the letter on its flag."""

    def __init__(self, stop: SingleStopPoint, arrivals: list[BusArrival], style: Style = "classic") -> None:
        """`stop` and its `arrivals` from TfL, drawn in `style`."""
        super().__init__(arrivals, style)
        self.stop = stop

    @property
    @override
    def stop_name(self) -> str:
        return self.stop.common_name

    @property
    @override
    def stop_letter(self) -> str | None:
        return self.stop.stop_letter

    @staticmethod
    @override
    def _route_key(arrival: BusArrival) -> tuple[str, str]:
        return arrival.line, ""

    @staticmethod
    @override
    def _badge(arrival: BusArrival) -> str:
        return arrival.line

    @staticmethod
    @override
    def _destination(arrival: BusArrival) -> str:
        return arrival.destination

    @override
    def add_header(self, img: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        draw.text((7, 10), self.stop.common_name, "white", font_size=15, anchor="lm")
        draw.text((img.size[0] - 9, 10), self.stop.stop_letter, "white", anchor="rm", stroke_width=0.2)
