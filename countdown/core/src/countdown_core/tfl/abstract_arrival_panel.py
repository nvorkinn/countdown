"""What every stop's departures panel shares, whichever source (TfL, Entur) its stops come from."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, override

from countdown_core.core.panel import Panel
from countdown_core.tfl.arrival_styles import STYLES

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.utils.clock import ClockFormat

# How a stop's departures are drawn: the config's departures.style (see DeparturesSettings.from_config for classic).
Style = Literal["hero", "grouped", "card"]
# What the styles that show one draw beside a stop's name.
Icon = Literal["bus", "subway", "tram", "train", "ferry"]


class Arrival(Protocol):
    """What the panels need of a departure, whichever source it came from."""

    time_to_station: int  # seconds


@dataclass(frozen=True)
class Departures:
    """Everything leaving a stop for one route and destination, soonest first."""

    badge: str  # what the route badge says: the bus's number, or the tube line's initial
    line: str
    destination: str
    minutes: tuple[int, ...]


class AbstractArrivalPanel(Panel, ABC):
    """One stop's departures, one Departures per route (and, on the tube, per destination), the soonest route first.

    Subclasses adapt a source's stops and arrivals (TfL's, Entur's) to it.
    """

    def __init__(self, arrivals: list[Arrival], style: Style = "hero") -> None:
        """Group `arrivals` (the stop's, in any order) into Departures, drawn in `style`."""
        super().__init__()
        self.style = style
        groups: dict[tuple[str, str], tuple[str, str, str, list[int]]] = {}
        for arrival in sorted(arrivals, key=lambda a: a.time_to_station):
            *_, minutes = groups.setdefault(
                self._route_key(arrival), (self._badge(arrival), arrival.line, self._destination(arrival), [])
            )
            minutes.append(arrival.time_to_station // 60)
        self.departures = [Departures(*group[:3], tuple(group[3])) for group in groups.values()]

    @property
    @abstractmethod
    def stop_name(self) -> str:
        """The stop's name, as its header shows it."""

    @property
    def stop_letter(self) -> str | None:
        """The letter on a bus stop's flag (or a platform's number), or None if it has none."""
        return None

    @property
    def is_tube(self) -> bool:
        """A London Underground station: the roundel, and trains told apart by destination."""
        return False

    @property
    def icon(self) -> Icon:
        """The kind of transport the stop serves, for the styles that draw it beside its name."""
        return "subway" if self.is_tube else "bus"

    @staticmethod
    @abstractmethod
    def _route_key(arrival: Arrival) -> tuple[str, str]:
        """Arrivals sharing a key are one row: later buses on a route, later trains to a destination."""

    @staticmethod
    @abstractmethod
    def _badge(arrival: Arrival) -> str: ...

    @staticmethod
    @abstractmethod
    def _destination(arrival: Arrival) -> str: ...

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        return STYLES[self.style](self, image_width, image_height)
