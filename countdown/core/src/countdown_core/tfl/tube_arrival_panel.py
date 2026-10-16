"""A Tube station's departures, a row per line and destination."""

from typing import TYPE_CHECKING, override

from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Style
from countdown_core.utils.utils import ROUNDEL

if TYPE_CHECKING:
    from PIL import Image, ImageDraw

    from countdown_core.tfl.models import MetroStopPoint, TubeArrival


class TubeArrivalPanel(AbstractArrivalPanel):
    """A Tube station's departures, headed by the roundel and its name."""

    def __init__(self, stop: MetroStopPoint, arrivals: list[TubeArrival], style: Style = "classic") -> None:
        """`stop` and its `arrivals` from TfL, drawn in `style`."""
        # Trains terminating here aren't departures.
        super().__init__([a for a in arrivals if a.naptan_id != a.destination_naptan_id], style)
        self.stop = stop

    @property
    @override
    def stop_name(self) -> str:
        return self.stop.common_name.removesuffix(" Underground Station")

    @property
    @override
    def is_tube(self) -> bool:
        return True

    @staticmethod
    @override
    def _route_key(arrival: TubeArrival) -> tuple[str, str]:
        return arrival.line, arrival.towards

    @staticmethod
    @override
    def _badge(arrival: TubeArrival) -> str:
        return arrival.line[0]

    @staticmethod
    @override
    def _destination(arrival: TubeArrival) -> str:
        return arrival.towards

    @override
    def add_header(self, img: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        img.paste(ROUNDEL, (5, 2), ROUNDEL)
        draw.text((10 + ROUNDEL.size[0], 10), self.stop_name, "white", font_size=15, anchor="lm")
