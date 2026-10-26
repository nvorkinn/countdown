"""A stop as one side of the HD landscape screen's departure board (rendering.layouts.hd_landscape_board).

A black bar with the modes the stop serves, its name in capitals and "MIN" over the times' column; under it a row per
route (on the tube, per destination), the soonest first: the route's badge (filled for the soonest), where it's going
and when it next leaves, right-aligned, with a dotted rule under each row. The soonest route says when the two after it
leave too. Laid out in pixels sized for the 7.5" screens; the layout draws it two and a quarter times as big, as the
landscape sidebar layout draws its stops.
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.panel import Panel
from countdown_renderer.tfl.arrival_styles import (
    BOLD,
    ICONS,
    MAX_HEADER_ICONS,
    MEDIUM,
    REGULAR,
    ROW_ICON_ROOM,
    SANS,
    SANS_SEMI,
    _later,
    _minutes,
    _shrunk,
)
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import fitted_font, google_symbols, resized, truncate_to_fit
from countdown_shared.i18n import _

if TYPE_CHECKING:
    from PIL import Image

    from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel, Departures
    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import ScaledDraw

# The black bar, and a route's row under it.
BAR = 34
ROW = 40
# Inside the bar and the rows: the room at either end; the mode icons' size and how far apart they are.
INSET = 10
ICON_SIZE = 24
ICON_STEP = 28
# A route's badge: its box (from the panel's left edge, with a little room for its outline), and where its
# destination starts.
BADGE_LEFT = 4
BADGE_WIDTH = 54
BADGE_HEIGHT = 26
DESTINATION_X = BADGE_LEFT + BADGE_WIDTH + 12
# Between a destination and the time after it, at the least.
TIME_GAP = 12
# Every how many pixels the dotted rule under a row has a dot.
DOT_STEP = 4


class BoardStopPanel(Panel):
    """One stop's departures as a departure board: a black bar, then a row per route."""

    scales = True

    def __init__(self, stop: AbstractArrivalPanel) -> None:
        """`stop`: the stop's departures panel, whose departures (soonest route first) the rows show."""
        super().__init__()
        self.stop = stop

    def rows_fitting(self, height: int) -> int:
        """Return how many routes' rows are drawn in a panel `height` tall: as many as fit, up to the stop's routes."""
        return max(0, min(len(self.stop.departures), (height - BAR) // ROW))

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        _draw_bar(d, self.stop, image_width)
        if not self.stop.departures:
            d.text((BADGE_LEFT, BAR + ROW / 2), _("No departures"), "black", fitted_font(SANS, 16), anchor="lm")
            return img
        # A stop serving several modes says each row's before its destination.
        icon_room = ROW_ICON_ROOM if self.stop.mixed_modes else 0
        y = BAR
        for i, route in enumerate(self.stop.departures[: self.rows_fitting(image_height)]):
            _draw_row(d, route, (y, image_width), first=i == 0, icon_room=icon_room)
            y += ROW
        return img


def _draw_bar(d: ScaledDraw, stop: AbstractArrivalPanel, width: int) -> None:
    """Draw the stop's black bar: the modes it serves, its name in capitals, and "MIN" over the times."""
    d.rectangle((0, 0, width - 1, BAR - 1), fill="black")
    middle = BAR / 2
    x = INSET
    for icon in stop.icons[:MAX_HEADER_ICONS]:
        d.text((x, middle), ICONS[icon], "white", resized(google_symbols(), ICON_SIZE), anchor="lm")
        x += ICON_STEP
    unit_font = fitted_font(MEDIUM, 13)
    unit = _("min").upper()
    d.text((width - INSET, middle + 1), unit, "white", unit_font, anchor="rm")
    name_font = fitted_font(BOLD, 19)
    room = width - INSET - unit_font.getlength(unit) - 16 - (x + 6)
    name = truncate_to_fit(stop.stop_name.upper(), name_font, room)
    d.text((x + 6, middle + 1), name, "white", name_font, anchor="lm")


def _draw_row(d: ScaledDraw, route: Departures, at: tuple[float, int], *, first: bool, icon_room: int) -> None:
    """Draw a route's row with its top and the panel's width `at`: the badge, the destination, the time and a rule.

    The `first` (the stop's soonest route) has its badge filled, its time bigger, and the next two times under its
    destination; `icon_room` is the room before the destination for the row's mode, if the stop shows them.
    """
    y, width = at
    middle = y + ROW / 2
    box = (BADGE_LEFT, middle - BADGE_HEIGHT / 2, BADGE_LEFT + BADGE_WIDTH, middle + BADGE_HEIGHT / 2)
    badge_font = _shrunk(route.badge, BOLD, 16, BADGE_WIDTH - 8)
    centre = (BADGE_LEFT + BADGE_WIDTH / 2, middle)
    if first:
        d.rounded_rectangle(box, 5, fill="black")
        d.text(centre, route.badge, "white", badge_font, anchor="mm")
    else:
        d.rounded_rectangle(box, 5, outline="black", width=2)
        d.text(centre, route.badge, "black", badge_font, anchor="mm")

    minutes = route.minutes[0]
    time = _minutes(minutes)
    # "due" is a word, set a little smaller than a number.
    time_font = fitted_font(BOLD, (28 if first else 22) - (4 if minutes == 0 else 0))
    right = width - INSET
    d.text((right, middle + 1), time, "black", time_font, anchor="rm")

    x = DESTINATION_X
    if icon_room and route.icon:
        d.text((x, middle), ICONS[route.icon], "black", resized(google_symbols(), 18), anchor="lm")
        x += icon_room
    room = right - time_font.getlength(time) - TIME_GAP - x
    destination_font = fitted_font(SANS_SEMI, 17)
    later = _later(route.minutes) if first else ""
    destination_y = middle - 6 if later else middle
    destination = truncate_to_fit(route.destination, destination_font, room)
    d.text((x, destination_y), destination, "black", destination_font, anchor="lm")
    if later:
        d.text(
            (x, middle + 11),
            truncate_to_fit(later, fitted_font(REGULAR, 12), room),
            "black",
            fitted_font(REGULAR, 12),
            anchor="lm",
        )

    for dot in range(BADGE_LEFT, width, DOT_STEP):
        d.point((dot, y + ROW - 1), fill="black")
