"""The stops' departures along a line of time: now at the top, half an hour down, each departure at its minute.

The first stop's departures hang off the line's left side and the second's off its right, so the gaps between them
show at a glance. Each sits as near its minute as the one above leaves room for, with a leader to the exact minute.
Laid out in pixels sized for the 7.5" screens; the HD screen's timeline layout draws it twice as big.
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_core.tfl.arrival_styles import BOLD, ICONS, MEDIUM, SANS, SANS_SEMI, _shrunk
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import fitted_font, google_symbols, resized, truncate_to_fit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.core.panel import Panel as AnyPanel
    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw

# How far down the line goes, in minutes, and where its minutes are marked.
WINDOW = 30
TICK = 5
# The stops' names over the line take this much; the line starts under them, its "now" marker over its top.
HEADER = 40
NOW_ROOM = 24
# Half the line's width (it's two rules, the minutes marked between them), and the room either side of it.
HALF = 17
SIDE_GAP = 26
# A departure's row: how far apart two are at least, and its badge's size.
ROW = 26
BADGE = (46, 21)

# A departure on the line: its route's badge, where it's going, and in how many minutes.
Departure = tuple[str, str, int]


def departures(stop: AbstractArrivalPanel, window: int = WINDOW) -> list[Departure]:
    """Return every departure from `stop` in the next `window` minutes, soonest first."""
    found = [
        (route.badge, route.destination, minutes)
        for route in stop.departures
        for minutes in route.minutes
        if minutes <= window
    ]
    return sorted(found, key=lambda departure: departure[2])


def placed(minutes: Sequence[int], top: float, per_minute: float, spacing: float) -> list[float]:
    """Return each row's y: at its minute along the line, pushed down to clear the row above."""
    ys: list[float] = []
    for minute in minutes:
        y = top + minute * per_minute
        ys.append(max(y, ys[-1] + spacing) if ys else y)
    return ys


class TimelinePanel(Panel):
    """Up to two stops' next half hour, one either side of a line of time."""

    scales = True

    def __init__(self, stops: Sequence[AnyPanel]) -> None:
        """Take the stops on screen (`stops`); the first two with departures to show are drawn."""
        super().__init__()
        self.stops = [stop for stop in stops if isinstance(stop, AbstractArrivalPanel) and stop.departures][:2]

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        axis = image_width / 2
        top, bottom = HEADER + NOW_ROOM, image_height - 22
        per_minute = (bottom - top) / WINDOW

        d.line((axis - HALF, top, axis - HALF, bottom), fill="black", width=2)
        d.line((axis + HALF, top, axis + HALF, bottom), fill="black", width=2)
        for minute in range(TICK, WINDOW + 1, TICK):
            y = top + minute * per_minute
            d.line((axis - HALF - 8, y, axis - HALF, y), fill="black", width=2)
            d.line((axis + HALF, y, axis + HALF + 8, y), fill="black", width=2)
            d.text((axis, y), str(minute), "black", fitted_font(SANS_SEMI, 13), anchor="mm")
        d.rounded_rectangle((axis - HALF - 7, top - 11, axis + HALF + 7, top + 11), 5, fill="black")
        d.text((axis, top), _("NOW"), "white", _shrunk(_("NOW"), BOLD, 11, 2 * HALF + 10), anchor="mm")
        d.text((axis, bottom + 13), _("min"), "black", fitted_font(SANS, 12), anchor="mm")

        for side, stop in enumerate(self.stops):
            self._draw_side(d, stop, side, (image_width, axis), (top, bottom, per_minute))
        return img

    @staticmethod
    def _draw_side(
        d: ScaledDraw,
        stop: AbstractArrivalPanel,
        side: int,
        across: tuple[float, float],
        down: tuple[float, float, float],
    ) -> None:
        """Draw `stop`'s name over its side of the line (0 the left, 1 the right) and its departures along it.

        `across` is the panel's width and the line's x; `down` is where the line starts and ends, and how far apart
        its minutes are.
        """
        width, axis = across
        top, bottom, per_minute = down
        edge = axis - HALF if side == 0 else axis + HALF
        near = edge - SIDE_GAP if side == 0 else edge + SIDE_GAP
        x0, x1 = (0.0, near) if side == 0 else (near, width)

        name_font = fitted_font(MEDIUM, 20)
        icon = ICONS[stop.icon]
        icon_font = resized(google_symbols(), 22)
        name = truncate_to_fit(stop.stop_name, name_font, x1 - x0 - 30)
        if side == 0:
            d.text((x1, 18), name, "black", name_font, anchor="rm")
            d.text((x1 - name_font.getlength(name) - 6, 18), icon, "black", icon_font, anchor="rm")
        else:
            d.text((x0, 18), icon, "black", icon_font, anchor="lm")
            d.text((x0 + 28, 18), name, "black", name_font, anchor="lm")

        rows = departures(stop)
        for i, ((badge, destination, minutes), y) in enumerate(
            zip(rows, placed([row[2] for row in rows], top, per_minute, ROW), strict=True)
        ):
            if y > bottom + 4:
                break
            first = i == 0
            box = (x0, y - BADGE[1] / 2, x0 + BADGE[0], y + BADGE[1] / 2)
            if first:
                d.rounded_rectangle(box, 4, fill="black")
            else:
                d.rounded_rectangle(box, 4, outline="black", width=2)
            badge_font = _shrunk(badge, BOLD, 13, BADGE[0] - 6)
            d.text((x0 + BADGE[0] / 2, y), badge, "white" if first else "black", badge_font, anchor="mm")
            when = _("due") if minutes == 0 else f"{minutes} {_('min')}"
            when_font = fitted_font(SANS_SEMI, 15)
            d.text((x1, y), when, "black", when_font, anchor="rm")
            destination_font = fitted_font(SANS_SEMI if first else SANS, 15)
            room = x1 - x0 - BADGE[0] - 10 - when_font.getlength(when) - 10
            d.text(
                (x0 + BADGE[0] + 9, y),
                truncate_to_fit(destination, destination_font, room),
                "black",
                destination_font,
                anchor="lm",
            )
            # A leader from the row to its minute on the line, and a dot there.
            exact = top + minutes * per_minute
            start, end = (x1 + 3, edge - 5) if side == 0 else (edge + 5, x0 - 3)
            d.line((start, y, end, exact) if side == 0 else (start, exact, end, y), fill="black", width=1)
            dot = edge - 4 if side == 0 else edge + 4
            d.ellipse((dot - 3, exact - 3, dot + 2, exact + 2), fill="black")
