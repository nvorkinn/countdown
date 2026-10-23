"""The stops as a departures board: each route once, with its next three times in columns.

Each stop has a black bar with its name, and a row per route (on the tube, per destination), the soonest first: the
route's badge, where it's going, then when it next leaves, big, and the two after. Laid out in pixels sized for the
7.5" screens; the HD screen's board layout draws it twice as big.
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_core.tfl.arrival_styles import BOLD, ICONS, MAX_HEADER_ICONS, MEDIUM, SANS, SANS_SEMI, _shrunk
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import fitted_font, google_symbols, resized, truncate_to_fit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.core.panel import Panel as AnyPanel
    from countdown_core.tfl.abstract_arrival_panel import Departures
    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw

# A stop's bar, a route's row, and the gap between two stops.
BAR = 34
ROW = 28
STOP_GAP = 14
# The times' columns, by their right edges from the panel's: the next (with its unit after it), then the two after.
NEXT_RIGHT = 190
THEN_RIGHTS = (85, 6)
BADGE_WIDTH = 50


def rows_each(routes: Sequence[int], rows: int) -> list[int]:
    """Share `rows` between stops with `routes` routes each: evenly, then what a stop can't use to the others."""
    shares = [0] * len(routes)
    left = rows
    while left > 0:
        wanting = [i for i, count in enumerate(routes) if shares[i] < count]
        if not wanting:
            break
        each = max(1, left // len(wanting))
        for i in wanting:
            give = min(each, routes[i] - shares[i], left)
            shares[i] += give
            left -= give
    return shares


class RouteBoardPanel(Panel):
    """The stops on screen, one under the other, each route once with its next three times."""

    scales = True

    def __init__(self, stops: Sequence[AnyPanel]) -> None:
        """Take the stops on screen (`stops`); those with departures to show are drawn."""
        super().__init__()
        self.stops = [stop for stop in stops if isinstance(stop, AbstractArrivalPanel) and stop.departures]

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        if not self.stops:
            return img
        room = image_height - len(self.stops) * BAR - (len(self.stops) - 1) * STOP_GAP
        shares = rows_each([len(stop.departures) for stop in self.stops], max(0, room // ROW))
        y = 0
        for stop, share in zip(self.stops, shares, strict=True):
            self._draw_bar(d, stop, y, image_width)
            y += BAR
            for i, route in enumerate(stop.departures[:share]):
                self._draw_row(d, route, y, image_width, first=i == 0)
                y += ROW
            y += STOP_GAP
        return img

    @staticmethod
    def _draw_bar(d: ScaledDraw, stop: AbstractArrivalPanel, y: float, width: int) -> None:
        """Draw the stop's black bar: the modes it serves and its name, and over the times' columns their names."""
        d.rectangle((0, y, width - 1, y + BAR - 1), fill="black")
        middle = y + BAR / 2
        x = 10
        for icon in stop.icons[:MAX_HEADER_ICONS]:
            d.text((x, middle), ICONS[icon], "white", resized(google_symbols(), 22), anchor="lm")
            x += 26
        label_font = fitted_font(SANS_SEMI, 11)
        next_x = width - NEXT_RIGHT + 24
        d.text((next_x, middle), _("NEXT"), "white", label_font, anchor="rm")
        d.text((width - (THEN_RIGHTS[0] + THEN_RIGHTS[1]) / 2 - 20, middle), _("THEN"), "white", label_font, "mm")
        name_font = fitted_font(MEDIUM, 20)
        name = truncate_to_fit(stop.stop_name, name_font, next_x - label_font.getlength(_("NEXT")) - 20 - (x + 4))
        d.text((x + 4, middle + 1), name, "white", name_font, anchor="lm")

    @staticmethod
    def _draw_row(d: ScaledDraw, route: Departures, y: float, width: int, *, first: bool) -> None:
        """Draw a route's row with its top at `y`: its badge (filled for the stop's soonest), destination and times."""
        middle = y + ROW / 2
        box = (2, middle - 10, 2 + BADGE_WIDTH, middle + 10)
        if first:
            d.rounded_rectangle(box, 4, fill="black")
        else:
            d.rounded_rectangle(box, 4, outline="black", width=2)
        badge_font = _shrunk(route.badge, BOLD, 13, BADGE_WIDTH - 6)
        d.text((2 + BADGE_WIDTH / 2, middle), route.badge, "white" if first else "black", badge_font, anchor="mm")

        next_right = width - NEXT_RIGHT
        destination_font = fitted_font(SANS, 16)
        room = next_right - 40 - (BADGE_WIDTH + 14)
        d.text(
            (BADGE_WIDTH + 14, middle),
            truncate_to_fit(route.destination, destination_font, room),
            "black",
            destination_font,
            anchor="lm",
        )
        soonest, *later = route.minutes
        if soonest == 0:
            d.text((next_right + 22, middle), _("due"), "black", fitted_font(BOLD, 18), anchor="rm")
        else:
            d.text((next_right, middle + 7), str(soonest), "black", fitted_font(BOLD, 20), anchor="rs")
            d.text((next_right + 3, middle + 7), _("min"), "black", fitted_font(SANS, 11), anchor="ls")
        for minutes, right in zip(later[:2], THEN_RIGHTS, strict=False):
            d.text((width - right, middle), str(minutes), "black", fitted_font(SANS, 16), anchor="rm")
        for x in range(BADGE_WIDTH + 14, width, 4):
            d.point((x, y + ROW - 1), fill="black")
