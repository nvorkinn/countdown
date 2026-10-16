from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from PIL import Image, ImageDraw, ImageFont

from countdown_core.core.panel import Panel
from countdown_core.i18n import _, ngettext
from countdown_core.tfl.arrival_styles import STYLES
from countdown_core.utils.utils import UBUNTU_BOLD, UBUNTU_CONDENSED, UBUNTU_MEDIUM, truncate_to_fit

if TYPE_CHECKING:
    from countdown_core.utils.clock import ClockFormat

# How a stop's departures are drawn: the config's departures.style.
Style = Literal["classic", "hero", "grouped", "card"]
# What the styles that show one draw beside a stop's name.
Icon = Literal["bus", "subway", "tram", "train", "ferry"]


class Arrival(Protocol):
    """What the panels need of a departure, whichever source it came from."""

    time_to_station: int  # seconds


# One departure per row: a route badge (square-cornered -- on a 1-bit screen only straight
# edges come out crisp), the destination, and the time on the right.
ROW_HEIGHT = 24
ROW_GAP = 3  # between departures
# Every badge is the same width. Routes that don't fit in UBUNTU_MEDIUM ("N155", "SL10")
# are drawn in UBUNTU_CONDENSED: same height, narrower.
BADGE_WIDTH = 36
BADGE_PADDING = 3  # either side of the route inside its badge
DESTINATION_GAP = 6  # between the badge and the destination


@dataclass(frozen=True)
class Departures:
    """Everything leaving a stop for one route and destination, soonest first."""

    badge: str  # what the route badge says: the bus's number, or the tube line's initial
    line: str
    destination: str
    minutes: tuple[int, ...]


class AbstractArrivalPanel(Panel, ABC):
    """One stop's departures, one Departures per route (and, on the tube, per destination),
    the soonest route first. Subclasses adapt a source's stops and arrivals (TfL's, Entur's) to it.
    """

    def __init__(self, arrivals: list[Arrival], style: Style = "classic"):
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
    def stop_name(self) -> str: ...

    @property
    def stop_letter(self) -> str | None:
        return None

    @property
    def is_tube(self) -> bool:
        """A London Underground station: the roundel, and trains told apart by destination."""
        return False

    @property
    def icon(self) -> Icon:
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

    def render(self, image_width, image_height, *, clock_format: ClockFormat = "24h") -> Image.Image:
        if self.style != "classic":
            return STYLES[self.style](self, image_width, image_height)
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        draw.font = UBUNTU_BOLD
        draw.rounded_rectangle((0, 0, img.size[0], 20), 10, "black")
        self.add_header(img, draw)
        img.paste(self._create_panel_for_arrivals(img.size[0], img.size[1] - 25), (0, 25))
        return img

    @abstractmethod
    def add_header(self, img: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        msg = "Subclasses must implement the add_header method."
        raise NotImplementedError(msg)

    def _create_panel_for_arrivals(self, max_x: int, max_y: int) -> Image.Image:
        img = Image.new("RGBA", (max_x, max_y), (255, 255, 255, 0))
        y = 0
        for departure in self.departures:
            eta = self._get_time_text(departure.minutes[0])
            arrival_panel = self._create_panel_for_stop_arrival(departure.badge, departure.destination, eta, max_x)
            arrival_panel_bottom = y + arrival_panel.size[1]
            if arrival_panel_bottom > img.size[1]:
                return img
            img.paste(arrival_panel, (0, y))
            y = arrival_panel_bottom + ROW_GAP
        return img

    @staticmethod
    def _route_font(route: str) -> ImageFont.FreeTypeFont:
        """The usual font if the route fits its badge, otherwise the condensed one."""
        if UBUNTU_MEDIUM.getlength(route) <= BADGE_WIDTH - 2 * BADGE_PADDING:
            return UBUNTU_MEDIUM
        return UBUNTU_CONDENSED

    @staticmethod
    def _create_panel_for_stop_arrival(route: str, destination: str, eta: str, max_x: int) -> Image.Image:
        img = Image.new("RGBA", (max_x, ROW_HEIGHT), (255, 255, 255, 0))
        d = ImageDraw.Draw(img)
        d.fontmode = "1"
        middle = ROW_HEIGHT // 2
        d.rectangle((0, 0, BADGE_WIDTH - 1, ROW_HEIGHT - 1), fill="black")
        d.text((BADGE_WIDTH / 2, middle), route, "white", font=AbstractArrivalPanel._route_font(route), anchor="mm")
        # Cut short rather than run into the time, on a stop narrowed to fit (a portrait screen).
        destination_x = BADGE_WIDTH + DESTINATION_GAP
        room = img.size[0] - destination_x - UBUNTU_MEDIUM.getlength(eta) - DESTINATION_GAP
        destination = truncate_to_fit(destination, UBUNTU_BOLD, room)
        d.text((destination_x, middle), destination, "black", font=UBUNTU_BOLD, anchor="lm")
        d.text((img.size[0], middle), eta, "black", font=UBUNTU_MEDIUM, anchor="rm")
        return img

    @staticmethod
    def _get_time_text(minutes: int) -> str:
        if minutes == 0:
            return _("due")
        return ngettext("{n} min", "{n} mins", minutes).format(n=minutes)
