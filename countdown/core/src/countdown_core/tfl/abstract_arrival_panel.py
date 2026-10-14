from abc import ABC, abstractmethod

from PIL import Image, ImageDraw, ImageFont

from countdown_core.core.panel import Panel
from countdown_core.spotify.spotify_panel import truncate_to_fit
from countdown_core.tfl.models import ArrivalUnion
from countdown_core.utils.utils import UBUNTU_BOLD, UBUNTU_CONDENSED, UBUNTU_MEDIUM

# One departure per row: a route badge (square-cornered -- on a 1-bit screen only straight
# edges come out crisp), the destination, and the time on the right.
ROW_HEIGHT = 24
ROW_GAP = 3  # between departures
# Every badge is the same width. Routes that don't fit in UBUNTU_MEDIUM ("N155", "SL10")
# are drawn in UBUNTU_CONDENSED: same height, narrower.
BADGE_WIDTH = 36
BADGE_PADDING = 3  # either side of the route inside its badge
DESTINATION_GAP = 6  # between the badge and the destination


class AbstractArrivalPanel(Panel, ABC):
    def __init__(self, arrivals: list[ArrivalUnion]):
        super().__init__()
        self.arrivals = arrivals

    def render(self, image_width, image_height) -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        draw.font = UBUNTU_BOLD
        draw.rounded_rectangle((0, 0, img.size[0], 20), 10, "black")
        self.add_header(img, draw)
        img.paste(self._create_panel_for_arrivals(self.arrivals, img.size[0], img.size[1] - 25), (0, 25))
        return img

    @abstractmethod
    def add_header(self, img: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        raise NotImplementedError("Subclasses must implement the add_header method.")

    @abstractmethod
    def _create_panel_for_arrivals(self, arrivals: list[ArrivalUnion], max_x: int, max_y: int) -> Image.Image:
        raise NotImplementedError("Subclasses must implement the _create_panel_for_arrivals method.")

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
        match minutes:
            case 0:
                return "due"
            case 1:
                return "1 min"
            case _:
                return f"{minutes} mins"
