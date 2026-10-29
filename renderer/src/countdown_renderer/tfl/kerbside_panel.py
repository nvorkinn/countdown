"""One stop's departures, big, for the kerbside layouts: in one of three styles the config's layout picks.

Each has the stop's header (its modes, name and letter over a rule), then rows that share the room under it, each
with the minutes big and light at the right:

- track: each route once, its badge and destination over a line of the next half hour with its departures on it, the
  next one filled and the ones after it ringed with their minute; a scale ("now", 5, 10, ...) over the first row.
- ledger: each route once, its badge and destination, with the times after the next one under the destination.
- queue: every departure on a row of its own, the soonest first.

Laid out in pixels sized for the 7.5" screens; the HD screen's kerbside layouts draw it bigger.
"""

from typing import TYPE_CHECKING, Literal, override

from countdown_renderer.panel import Panel
from countdown_renderer.tfl.arrival_styles import (
    BOLD,
    ICONS,
    MAX_HEADER_ICONS,
    MEDIUM,
    REGULAR,
    SANS_SEMI,
    _later,
    _minutes,
    _shrunk,
    _unit,
)
from countdown_renderer.tfl.combined_arrival_panel import SQUARE, SQUARE_GAP
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_REGULAR, fitted_font, google_symbols, resized, truncate_to_fit
from countdown_shared.i18n import _

if TYPE_CHECKING:
    from PIL import Image, ImageFont

    from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel, Departures
    from countdown_renderer.tfl.combined_arrival_panel import Rotation
    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import ScaledDraw

KerbsideStyle = Literal["track", "ledger", "queue"]
KERBSIDE_STYLES: tuple[KerbsideStyle, ...] = ("track", "ledger", "queue")

# The header: its height (the rule along its foot), the gap under it, its icons' size and step, its name's size, and
# the letter's circle's radius.
HEADER = 48
HEADER_GAP = 14
HEADER_ICON = 32
HEADER_ICON_STEP = 34
NAME_SIZE = 26
LETTER_RADIUS = 16
# The minutes at the right: the column they take, their size and their unit's.
TIME_COLUMN = 118
TIME_SIZE = 58
UNIT_SIZE = 18
# A route's badge: its height, its text's size, and its least width.
BADGE_HEIGHT = 34
BADGE_SIZE = 20
BADGE_WIDTH = 76
BADGE_GAP = 12
# The least a row may be, per style: as many rows as fit at that share the room. And the size of the destination in
# each, and the smallest it's shrunk to before it's cut.
LEAST_ROW: dict[KerbsideStyle, int] = {"track": 88, "ledger": 72, "queue": 64}
DESTINATION_SIZE: dict[KerbsideStyle, int] = {"track": 22, "ledger": 26, "queue": 24}
DESTINATION_LEAST: dict[KerbsideStyle, int] = {"track": 17, "ledger": 19, "queue": 18}
# The ledger's "then 9, 16 min" under the destination: its size, and how far the two move apart.
LATER_SIZE = 16
LATER_OFFSET = 8
LATER_BELOW = 16
# The track: the half hour it shows and its ticks; its scale's height over the first row and the size of its labels;
# the room either end of the line for the markers; a row's height; the markers' radii and the ringed minutes' size.
WINDOW = 30
TICK = 5
SCALE_HEIGHT = 24
SCALE_SIZE = 13
TRACK_INSET = 18
TRACK_ROW = 80
NEXT_RADIUS = 16
LATER_RADIUS = 14
RING_SIZE = 13


class KerbsidePanel(Panel):
    """One stop's departures in a kerbside style, as tall as the layout gives it."""

    scales = True

    def __init__(self, stop: AbstractArrivalPanel, style: KerbsideStyle, rotation: Rotation | None = None) -> None:
        """Take the `stop` to draw, the `style` to draw it in, and `rotation`, where it is among all the stops."""
        super().__init__()
        self.stop = stop
        self.style = style
        self.rotation = rotation

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        self._draw_header(d, image_width)
        y = HEADER + HEADER_GAP
        if not self.stop.departures:
            return img
        if self.style == "track":
            self._draw_track(d, y, image_width, image_height)
        elif self.style == "ledger":
            self._draw_ledger(d, y, image_width, image_height)
        else:
            self._draw_queue(d, y, image_width, image_height)
        return img

    # --- the header -------------------------------------------------------------------------------------------------

    def _draw_header(self, d: ScaledDraw, width: int) -> None:
        """Draw the stop's modes, its name and its letter (and the stops' rotation) over a rule."""
        stop = self.stop
        middle = HEADER / 2
        x = 0
        for icon in stop.icons[:MAX_HEADER_ICONS]:
            d.text((x, middle), ICONS[icon], "black", resized(google_symbols(), HEADER_ICON), anchor="lm")
            x += HEADER_ICON_STEP
        right = width
        if stop.stop_letter:
            centre = right - LETTER_RADIUS
            box = (centre - LETTER_RADIUS, middle - LETTER_RADIUS, centre + LETTER_RADIUS, middle + LETTER_RADIUS)
            d.ellipse(box, fill="black")
            d.text((centre, middle), stop.stop_letter, "white", fitted_font(BOLD, round(NAME_SIZE * 0.8)), "mm")
            right -= 2 * LETTER_RADIUS + BADGE_GAP
        if self.rotation:
            right = self._draw_rotation(d, right, middle) - BADGE_GAP
        name_font = fitted_font(SANS_SEMI, NAME_SIZE)
        d.text((x, middle), truncate_to_fit(stop.stop_name, name_font, right - x), "black", name_font, anchor="lm")
        d.line((0, HEADER - 2, width, HEADER - 2), fill="black", width=3)

    def _draw_rotation(self, d: ScaledDraw, right: int, middle: float) -> int:
        """Draw a square per page of stops, filled for the one on screen, ending at `right`; return where they start."""
        rotation = self.rotation
        if rotation is None:
            return right
        x = right - rotation.pages * (SQUARE + SQUARE_GAP) + SQUARE_GAP
        top = round(middle - SQUARE / 2)
        for page in range(rotation.pages):
            box = (x, top, x + SQUARE - 1, top + SQUARE - 1)
            if page == rotation.page:
                d.rectangle(box, fill="black")
            else:
                d.rectangle(box, outline="black", width=1)
            x += SQUARE + SQUARE_GAP
        return right - rotation.pages * (SQUARE + SQUARE_GAP) + SQUARE_GAP

    # --- what the rows share ----------------------------------------------------------------------------------------

    @staticmethod
    def _rows(top: float, bottom: int, count: int, least: int) -> tuple[int, float]:
        """Return how many of `count` rows fit between `top` and `bottom` at `least` tall, and each one's height."""
        shown = max(1, min(count, int((bottom - top) // least)))
        return shown, (bottom - top) / shown

    @staticmethod
    def _draw_badge(d: ScaledDraw, route: Departures, x: float, top: float) -> float:
        """Draw the route's badge, filled, with its top left at (`x`, `top`); return its width."""
        font = _shrunk(route.badge, BOLD, BADGE_SIZE, BADGE_WIDTH + 20)
        width = max(BADGE_WIDTH, font.getlength(route.badge) + 20)
        d.rounded_rectangle((x, top, x + width, top + BADGE_HEIGHT), 7, fill="black")
        d.text((x + width / 2, top + BADGE_HEIGHT / 2), route.badge, "white", font, anchor="mm")
        return width

    def _destination(self, text: str, room: float) -> tuple[str, ImageFont.FreeTypeFont]:
        """Return the destination as drawn in `room`: shrunk a little if it's long, then cut if it still doesn't fit."""
        size, least = DESTINATION_SIZE[self.style], DESTINATION_LEAST[self.style]
        font = fitted_font(MEDIUM, size)
        while font.getlength(text) > room and size > least:
            size -= 1
            font = fitted_font(MEDIUM, size)
        return truncate_to_fit(text, font, room), font

    @staticmethod
    def _draw_time(d: ScaledDraw, minutes: int, right: int, baseline: float) -> None:
        """Draw the minutes big and light, ending at `right` on `baseline`, with "min" small after them."""
        number, unit = _minutes(minutes), _unit(minutes, _("min"))
        number_font, unit_font = resized(GOOGLE_REGULAR, TIME_SIZE), resized(GOOGLE_REGULAR, UNIT_SIZE)
        x = float(right)
        if unit:
            d.text((x, baseline), unit, "black", unit_font, anchor="rs")
            x -= unit_font.getlength(unit) + 4
        d.text((x, baseline), number, "black", number_font, anchor="rs")

    @staticmethod
    def _draw_rule(d: ScaledDraw, y: float, width: int) -> None:
        d.line((0, y, width, y), fill="black", width=1)

    # --- the styles -------------------------------------------------------------------------------------------------

    def _draw_queue(self, d: ScaledDraw, y: float, width: int, height: int) -> None:
        """Draw every departure on a row of its own, the soonest first."""
        arrivals = sorted(
            ((minutes, route) for route in self.stop.departures for minutes in route.minutes), key=lambda a: a[0]
        )
        shown, row = self._rows(y, height, len(arrivals), LEAST_ROW["queue"])
        for i, (minutes, route) in enumerate(arrivals[:shown]):
            middle = y + row / 2
            badge_width = self._draw_badge(d, route, 0, middle - BADGE_HEIGHT / 2)
            x = badge_width + BADGE_GAP
            text, font = self._destination(route.destination, width - x - TIME_COLUMN)
            d.text((x, middle), text, "black", font, anchor="lm")
            self._draw_time(d, minutes, width, middle + TIME_SIZE * 0.38)
            if i < shown - 1:
                self._draw_rule(d, y + row - 1, width)
            y += row

    def _draw_ledger(self, d: ScaledDraw, y: float, width: int, height: int) -> None:
        """Draw each route once: its badge, destination and (under that) the times after the next, and the minutes."""
        routes = self.stop.departures
        shown, row = self._rows(y, height, len(routes), LEAST_ROW["ledger"])
        for i, route in enumerate(routes[:shown]):
            middle = y + row / 2
            badge_width = self._draw_badge(d, route, 0, middle - BADGE_HEIGHT / 2)
            x = badge_width + BADGE_GAP
            text, font = self._destination(route.destination, width - x - TIME_COLUMN)
            later = _later(route.minutes)
            d.text((x, middle - LATER_OFFSET if later else middle), text, "black", font, anchor="lm")
            if later:
                d.text((x, middle + LATER_BELOW), later, "black", fitted_font(REGULAR, LATER_SIZE), anchor="lm")
            self._draw_time(d, route.minutes[0], width, middle + TIME_SIZE * 0.38)
            if i < shown - 1:
                self._draw_rule(d, y + row - 1, width)
            y += row

    def _draw_track(self, d: ScaledDraw, y: float, width: int, height: int) -> None:
        """Draw a scale of the next half hour, then each route once with its departures along a line of it."""
        track_right = width - TIME_COLUMN
        y = self._draw_scale(d, y, track_right)
        routes = self.stop.departures
        shown, row = self._rows(y, height, len(routes), LEAST_ROW["track"])
        for i, route in enumerate(routes[:shown]):
            top = y + (row - TRACK_ROW) / 2
            badge_width = self._draw_badge(d, route, 0, top + 2)
            x = badge_width + BADGE_GAP
            text, font = self._destination(route.destination, width - x - TIME_COLUMN)
            d.text((x, top + 2 + BADGE_HEIGHT / 2), text, "black", font, anchor="lm")
            self._draw_line(d, route, top + TRACK_ROW * 0.72, track_right)
            self._draw_time(d, route.minutes[0], width, top + TRACK_ROW * 0.72 + TIME_SIZE * 0.3)
            if i < shown - 1:
                self._draw_rule(d, y + row - 1, width)
            y += row

    @staticmethod
    def _at(minutes: int, track_right: int) -> float:
        """Where `minutes` along the line is, from the panel's left edge."""
        return TRACK_INSET + (track_right - 2 * TRACK_INSET) * minutes / WINDOW

    def _draw_scale(self, d: ScaledDraw, y: float, track_right: int) -> float:
        """Draw the scale over the first row: "now", then every five minutes, on a rule; return where the rows start."""
        font = resized(GOOGLE_REGULAR, SCALE_SIZE)
        d.line((0, y + SCALE_HEIGHT - 2, track_right, y + SCALE_HEIGHT - 2), fill="black", width=1)
        for minutes in range(0, WINDOW + 1, TICK):
            label = _("now") if minutes == 0 else str(minutes)
            d.text((self._at(minutes, track_right), y + SCALE_HEIGHT / 2 - 1), label, "black", font, anchor="mm")
        return y + SCALE_HEIGHT

    def _draw_line(self, d: ScaledDraw, route: Departures, y: float, track_right: int) -> None:
        """Draw the route's line of the next half hour with its departures on it, centred on `y`."""
        d.line((self._at(0, track_right), y, self._at(WINDOW, track_right), y), fill="black", width=1)
        for minutes in range(0, WINDOW + 1, TICK):
            x = self._at(minutes, track_right)
            d.line((x, y - 5, x, y + 5), fill="black", width=1)
        for i, minutes in enumerate(route.minutes[:3]):
            if minutes > WINDOW:
                break
            x = self._at(minutes, track_right)
            if i == 0:
                d.ellipse((x - NEXT_RADIUS, y - NEXT_RADIUS, x + NEXT_RADIUS, y + NEXT_RADIUS), fill="black")
            else:
                box = (x - LATER_RADIUS, y - LATER_RADIUS, x + LATER_RADIUS, y + LATER_RADIUS)
                d.ellipse(box, fill="white", outline="black", width=2)
                d.text((x, y), str(minutes), "black", fitted_font(MEDIUM, RING_SIZE), anchor="mm")
