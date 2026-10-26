"""The stops as the HD landscape screen's focus layout draws them: each stop's next departure huge, the rest in lists.

Each stop has its modes and name over its next departure: the minutes big with "min" after them, then the route's
badge filled black and where it's going, with the two after it under that ("then 9, 16 min"). The stops' badges line
up. Under both, a rule, and a list per stop side by side ("ALSO FROM ..."): each other route's badge, where it's going,
and when it next leaves. Laid out in pixels sized for the 7.5" screens; the layout draws it about two and a quarter
times as big (rendering.layouts.hd_landscape_focus).
"""

from typing import TYPE_CHECKING, override

from countdown_core.i18n import _
from countdown_renderer.panel import Panel
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_renderer.tfl.arrival_styles import BOLD, ICONS, MAX_HEADER_ICONS, SANS, SANS_SEMI, _shrunk
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import fitted_font, google_symbols, resized, truncate_to_fit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image, ImageFont

    from countdown_renderer.panel import Panel as AnyPanel
    from countdown_renderer.tfl.abstract_arrival_panel import Departures
    from countdown_renderer.tfl.combined_arrival_panel import Rotation
    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import ScaledDraw

# The most stops drawn: one list each, side by side.
STOPS = 2
# A stop's header (its modes and name), and its next departure under it: how tall the two are, and the gap after them.
HEADER = 24
HERO = 92
HERO_GAP = 12
# The next departure: the minutes' size, where their baseline is under the header, the gap before their unit, and
# the badge's size and where it starts under the header.
MINUTES_SIZE = 72
MINUTES_BASELINE = 78
UNIT_GAP = 8
BADGE = (84, 42)
BADGE_TOP = 22
# The lists: the gap between two side by side, a list's title, a row's height, and a row's badge's size.
LIST_GAP = 26
TITLE = 22
ROW = 27
ROW_BADGE = (48, 22)
# The rotation's squares (a square per page of stops, filled for the one on screen), at the top right.
SQUARE = 8
SQUARE_GAP = 6


class FocusPanel(Panel):
    """Up to two stops: each one's next departure huge, then the rest of each in a list, side by side."""

    scales = True

    def __init__(self, stops: Sequence[AnyPanel], rotation: Rotation | None = None) -> None:
        """Take the stops on screen (`stops`): the first two that are stops are drawn; `rotation` as the stops have it.

        A stop with nothing leaving is drawn with its name, and says so.
        """
        super().__init__()
        self.stops = [stop for stop in stops if isinstance(stop, AbstractArrivalPanel)][:STOPS]
        self.rotation = rotation

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        if not self.stops:
            return img
        badge_x = self._badge_x()
        # The first stop's name leaves the rotation's squares their room.
        squares = self._draw_rotation(d, self.rotation, image_width) if self.rotation is not None else 0
        y = 0.0
        for i, stop in enumerate(self.stops):
            _draw_header(d, stop, y, image_width - (squares if i == 0 else 0))
            self._draw_hero(d, stop, y + HEADER, (badge_x, image_width))
            y += HEADER + HERO + HERO_GAP

        rows = int((image_height - y - TITLE - 4) // ROW)
        lists = self._lists(rows)
        if not any(routes for _title, routes in lists):
            return img
        d.line((0, y - HERO_GAP / 2, image_width, y - HERO_GAP / 2), fill="black", width=2)
        width = (image_width - LIST_GAP) / 2
        for i, (title, routes) in enumerate(lists):
            x = i * (width + LIST_GAP)
            if title and routes:
                d.text(
                    (x, y + 6),
                    truncate_to_fit(title, fitted_font(SANS_SEMI, 12), width),
                    "black",
                    fitted_font(SANS_SEMI, 12),
                )
            for row, route in enumerate(routes):
                self._draw_row(d, route, (x, y + TITLE + row * ROW), width)
        return img

    def _lists(self, rows: int) -> list[tuple[str | None, list[Departures]]]:
        """Return the two lists side by side, `rows` long at most: each stop's routes after its first, under a title.

        One stop's routes run on into the second list, with no title of its own, as evenly as `rows` lets them.
        """
        if rows <= 0:
            return []
        lists: list[tuple[str | None, list[Departures]]] = [
            (_("Also from {stop}").format(stop=stop.stop_name).upper(), list(stop.departures[1:][:rows]))
            for stop in self.stops
        ]
        if len(lists) == 1:
            title, _routes = lists[0]
            routes = self.stops[0].departures[1:]
            first = min(rows, -(-len(routes) // 2))
            lists = [(title, routes[:first]), (None, routes[first : first + rows])]
        return lists

    def _badge_x(self) -> float:
        """Return where the next departures' badges start: clear of the widest stop's minutes, so they line up."""
        widest = 0.0
        for stop in self.stops:
            if stop.departures:
                number, unit = _number_and_unit(stop.departures[0].minutes[0])
                widest = max(widest, _number_width(number, unit))
        return widest + 22

    @staticmethod
    def _draw_hero(d: ScaledDraw, stop: AbstractArrivalPanel, y: float, span: tuple[float, int]) -> None:
        """Draw the stop's next departure with its top at `y`; `span`: where its badge starts, and where it all ends."""
        badge_x, width = span
        if not stop.departures:
            d.text((0, y + 40), _("No departures"), "black", fitted_font(SANS, 22), anchor="ls")
            return
        route = stop.departures[0]
        number, unit = _number_and_unit(route.minutes[0])
        number_font = fitted_font(BOLD, MINUTES_SIZE)
        d.text((-2, y + MINUTES_BASELINE), number, "black", number_font, anchor="ls")
        if unit:
            d.text(
                (number_font.getlength(number) + UNIT_GAP, y + MINUTES_BASELINE),
                unit,
                "black",
                _unit_font(),
                anchor="ls",
            )

        badge_width, badge_height = BADGE
        box = (badge_x, y + BADGE_TOP, badge_x + badge_width, y + BADGE_TOP + badge_height)
        d.rounded_rectangle(box, 9, fill="black")
        badge_font = _shrunk(route.badge, BOLD, 30, badge_width - 10)
        d.text((badge_x + badge_width / 2, y + BADGE_TOP + badge_height / 2), route.badge, "white", badge_font, "mm")

        text_x = badge_x + badge_width + 14
        destination_font = _shrunk(route.destination, BOLD, 28, width - text_x)
        destination = truncate_to_fit(route.destination, destination_font, width - text_x)
        d.text((text_x, y + BADGE_TOP + 20), destination, "black", destination_font, anchor="ls")
        later = route.minutes[1:3]
        if later:
            then = _("then {times} min").format(times=", ".join(map(str, later)))
            then_font = fitted_font(SANS, 16)
            d.text(
                (text_x, y + BADGE_TOP + badge_height),
                truncate_to_fit(then, then_font, width - text_x),
                "black",
                then_font,
                anchor="ls",
            )

    @staticmethod
    def _draw_row(d: ScaledDraw, route: Departures, at: tuple[float, float], width: float) -> None:
        """Draw a route's row in a list, its top left `at`: its badge, where it's going, and when it next leaves."""
        x, y = at
        badge_width, badge_height = ROW_BADGE
        middle = y + badge_height / 2
        d.rounded_rectangle((x, y, x + badge_width, y + badge_height), 5, outline="black", width=2)
        badge_font = _shrunk(route.badge, BOLD, 14, badge_width - 8)
        d.text((x + badge_width / 2, middle), route.badge, "black", badge_font, anchor="mm")
        minutes = route.minutes[0]
        eta = _("due") if minutes == 0 else f"{minutes} {_('min')}"
        time_font = fitted_font(SANS_SEMI, 16)
        d.text((x + width, middle), eta, "black", time_font, anchor="rm")
        destination_font = fitted_font(SANS, 16)
        room = width - badge_width - 10 - time_font.getlength(eta) - 12
        destination = truncate_to_fit(route.destination, destination_font, room)
        d.text((x + badge_width + 10, middle), destination, "black", destination_font, anchor="lm")

    @staticmethod
    def _draw_rotation(d: ScaledDraw, rotation: Rotation, width: int) -> float:
        """Draw a square per page of stops at the first header's right end, filled for the page on screen.

        Returns the room they take.
        """
        room = rotation.pages * (SQUARE + SQUARE_GAP)
        x = width - room + SQUARE_GAP
        y = (HEADER - SQUARE) / 2
        for page in range(rotation.pages):
            box = (x, y, x + SQUARE - 1, y + SQUARE - 1)
            if page == rotation.page:
                d.rectangle(box, fill="black")
            else:
                d.rectangle(box, outline="black", width=1)
            x += SQUARE + SQUARE_GAP
        return room


def _draw_header(d: ScaledDraw, stop: AbstractArrivalPanel, top: float, right: float) -> None:
    """Draw the modes the stop serves and its name, with their top at `top` and ending by `right`."""
    middle = top + HEADER / 2
    x = 0
    for icon in stop.icons[:MAX_HEADER_ICONS]:
        d.text((x, middle), ICONS[icon], "black", resized(google_symbols(), 22), anchor="lm")
        x += 26
    name_font = fitted_font(SANS_SEMI, 16)
    d.text((x + 4, middle), truncate_to_fit(stop.stop_name.upper(), name_font, right - x - 4), "black", name_font, "lm")


def _number_and_unit(minutes: int) -> tuple[str, str]:
    """Return the next departure's minutes big and the unit after them: "due" and none, if it's due."""
    return (_("due"), "") if minutes == 0 else (str(minutes), _("min"))


def _unit_font() -> ImageFont.FreeTypeFont:
    return fitted_font(SANS, 24)


def _number_width(number: str, unit: str) -> float:
    """How wide the next departure's minutes and unit are, side by side."""
    width = fitted_font(BOLD, MINUTES_SIZE).getlength(number)
    return width + UNIT_GAP + _unit_font().getlength(unit) if unit else width
