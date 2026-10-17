"""What every stop's departures panel shares, whichever source (TfL, Entur) its stops come from."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, override

from countdown_core.core.panel import Panel
from countdown_core.tfl.arrival_styles import STYLES

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.core.style import Style
    from countdown_core.utils.clock import ClockFormat

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
    icon: Icon | None = None  # how this route travels, where the source says (a stop can serve several modes)


class AbstractArrivalPanel(Panel, ABC):
    """One stop's departures, one Departures per route (and, on the tube, per destination), the soonest route first.

    Subclasses adapt a source's stops and arrivals (TfL's, Entur's) to it.
    """

    def __init__(self, arrivals: list[Arrival], style: Style = "hero") -> None:
        """Group `arrivals` (the stop's, in any order) into Departures, drawn in `style`."""
        super().__init__()
        self.style = style
        groups: dict[tuple[str, str], tuple[str, str, str, Icon | None, list[int]]] = {}
        for arrival in sorted(arrivals, key=lambda a: a.time_to_station):
            *_, minutes = groups.setdefault(
                self._route_key(arrival),
                (self._badge(arrival), arrival.line, self._destination(arrival), self._icon(arrival), []),
            )
            minutes.append(arrival.time_to_station // 60)
        self.departures = [
            Departures(badge, line, destination, tuple(minutes), icon)
            for badge, line, destination, icon, minutes in groups.values()
        ]

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

    @property
    def icons(self) -> list[Icon]:
        """The kinds of transport the stop serves, each once, for the styles that draw them beside its name."""
        return [self.icon]

    @property
    def mixed_modes(self) -> bool:
        """Whether the departures span several modes, so each row says which it is."""
        return len({departure.icon for departure in self.departures if departure.icon}) > 1

    @staticmethod
    def _icon(arrival: Arrival) -> Icon | None:  # noqa: ARG004 -- a source that mixes modes overrides it
        """Return the kind of transport `arrival` is, or None where the stop serves only one."""
        return None

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
