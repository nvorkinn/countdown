"""The clock with the weather, as the HD screen's portrait layouts (beyond the classic one) draw them.

Three arrangements: a black band across the top (the masthead), a column down the left edge, and a header line over
the rest (the clock on the left and the weather on the right, or a big clock in the middle with the weather under it).
Each shows the render's moment (core.moment) in the screen's zone, and the weather carousel's pages at once, so
nothing in them rotates. They're laid out in pixels sized for the 7.5" screens, and the layouts draw them twice as big.
"""

from typing import TYPE_CHECKING, override

from countdown_renderer import moment
from countdown_renderer.panel import Panel
from countdown_renderer.utils.clock import format_clock
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, UBUNTU_BOLD
from countdown_renderer.weather.hd_sidebar_panel import current_page, later_rows, readings
from countdown_renderer.weather.hd_weather_panel import _fitted, _font, _icon, _stat, _temperature
from countdown_renderer.weather.weather_panel import _condition
from countdown_shared.i18n import _, format_date, local_zone

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import Fill, ScaledDraw
    from countdown_shared.weather.models import Weather

# Between a panel's edges and what's in it, in its own pixels.
MARGIN = 24
# The masthead's height, and the header line's (with the clock on the left, or big in the middle).
MASTHEAD_HEIGHT = 270
HEADER_HEIGHT = 96
CENTRED_HEADER_HEIGHT = 216
# The page squares' size and the gap between them, as the arrivals' rotation marker draws them, a little bigger.
SQUARE = 11
SQUARE_GAP = 7


def clock_and_date(clock_format: ClockFormat) -> tuple[str, str]:
    """Return the time and the date of the render's moment, in the screen's zone."""
    now = moment.now().astimezone(local_zone())
    # Translators: a CLDR date pattern, e.g. "Friday 16 October". Norwegian: "EEEE d. MMMM".
    return format_clock(now, clock_format), format_date(now.date(), _("EEEE d MMMM"))


def high_low(w: Weather) -> str:
    """Return the day's high and low, and the chance of rain if known, on one line."""
    line = f"{w.high:.0f}° / {w.low:.0f}°"
    if w.precipitation_probability is not None:
        line += "   " + _("Rain") + f" {w.precipitation_probability}%"
    return line


class _ClockPanel(Panel):
    """What the clock panels share: the carousel's pages, or what to say without them."""

    scales = True

    def __init__(self, pages: Sequence[Weather] = (), message: tuple[str, str] | None = None) -> None:
        """`pages`: the weather carousel's (now first), none if there's no forecast; `message`: then, what to say.

        `message` is a (title, text) pair, as a MessagePanel has them, both already marked for translation.
        """
        super().__init__()
        self.pages = tuple(pages)
        self.message = message


class HdMastheadPanel(_ClockPanel):
    """A black band: the time and date beside the weather now, its readings under them, and every later page."""

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height, (0, 0, 0, 255))
        left, right = MARGIN, image_width - MARGIN
        half = (right - left) / 2
        time, date = clock_and_date(clock_format)
        _fitted(d, (left, 88), time, UBUNTU_BOLD, 75, half, "ls", "white")
        _fitted(d, (left, 120), date, GOOGLE_REGULAR, 18, half, "ls", "white")

        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                title, text = self.message
                _fitted(d, (right, 70), _(title), GOOGLE_SEMI, 22, half, "rs", "white")
                _fitted(d, (right, 100), _(text), GOOGLE_REGULAR, 17, half, "rs", "white")
            return img

        width = _temperature(d, (right, 90), current, 80, space=half - 110, fill="white", right=True)
        _icon(d, current.weather_code, (right - width - 62, 58), 100, is_day=current.is_day, fill="white")
        _fitted(d, (right, 120), _condition(current), GOOGLE_REGULAR, 18, half, "rs", "white")
        d.line((left, 141, right, 141), fill="white", width=1)
        cells = readings(current, clock_format)
        cell = (right - left) / max(4, len(cells))
        for i, (label, value) in enumerate(cells):
            _stat(d, (left + i * cell, 152), label, value, (11, 17), cell - 12, "white")

        rows = later_rows(self.pages, clock_format)
        if not rows:
            return img
        d.line((left, 199, right, 199), fill="white", width=1)
        cell = (right - left) / max(3, len(rows))
        for i, (title, detail, code, is_day) in enumerate(rows):
            x = left + i * cell
            _fitted(d, (x, 211), title, GOOGLE_SEMI, 11, cell - 58, "la", "white")
            _fitted(d, (x, 244), detail, GOOGLE_REGULAR, 16, cell - 58, "ls", "white")
            _icon(d, code, (x + cell - 34, 226), 30, is_day=is_day, fill="white")
        return img


class HdWeatherColumnPanel(_ClockPanel):
    """A column: the time and date, the weather now big, the coming hours a row each, and every later page."""

    # The coming hours' rows, and the later pages'.
    HOUR_STEP = 30
    PAGE_STEP = 40

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        left, right = MARGIN, image_width - 4
        space = right - left
        time, date = clock_and_date(clock_format)
        _fitted(d, (left, 64), time, UBUNTU_BOLD, 56, space, "ls")
        _fitted(d, (left, 92), date, GOOGLE_REGULAR, 15, space, "ls")

        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                title, text = self.message
                _fitted(d, (left, 140), _(title), GOOGLE_SEMI, 18, space, "ls")
                _fitted(d, (left, 164), _(text), GOOGLE_REGULAR, 14, space, "ls")
            return img

        _icon(d, current.weather_code, (left + 70, 178), 140, is_day=current.is_day)
        _temperature(d, (left, 350), current, 105, space=space)
        _fitted(d, (left, 388), _condition(current), GOOGLE_REGULAR, 23, space, "ls")
        _fitted(d, (left, 414), high_low(current), GOOGLE_REGULAR, 15, space, "ls")

        rows = later_rows(self.pages, clock_format)
        pages_top = image_height - len(rows) * self.PAGE_STEP
        y = 432
        if current.hours:
            d.line((left, y, right, y), fill="black", width=1)
            y += 6
        for hour in current.hours:
            if y + self.HOUR_STEP > pages_top - 12:
                break
            middle = y + self.HOUR_STEP / 2
            _fitted(d, (left, middle), format_clock(hour.time, clock_format), GOOGLE_REGULAR, 15, 70, "lm")
            _icon(d, hour.weather_code, (left + 92, middle), 26, is_day=hour.is_day)
            if hour.precipitation_probability is not None:
                _fitted(d, (left + 160, middle), f"{hour.precipitation_probability}%", GOOGLE_REGULAR, 12, 40, "rm")
            d.text((right, middle), f"{round(hour.temperature)}°", "black", _font(GOOGLE_SEMI, 17), "rm")
            y += self.HOUR_STEP

        if not rows:
            return img
        y = pages_top
        d.line((left, y - 6, right, y - 6), fill="black", width=1)
        for title, detail, code, is_day in rows:
            _fitted(d, (left, y + 4), title, GOOGLE_SEMI, 11, space - 40, "la")
            _fitted(d, (left, y + 31), detail, GOOGLE_REGULAR, 14, space - 40, "ls")
            _icon(d, code, (right - 16, y + 17), 28, is_day=is_day)
            y += self.PAGE_STEP
        return img


class HdClockPanel(_ClockPanel):
    """A header: the time and date on the left and the weather now on the right, or the time big in the middle.

    Instead of the weather, the right can say something else (`summary`), and over the middle the squares say which
    of the layout's pages is on screen (`squares`: (the page, how many)).
    """

    def __init__(
        self,
        pages: Sequence[Weather] = (),
        message: tuple[str, str] | None = None,
        *,
        centred: bool = False,
        summary: str | None = None,
        squares: tuple[int, int] | None = None,
    ) -> None:
        """See _ClockPanel; `centred` sets the time big in the middle, with the weather on a line under it."""
        super().__init__(pages, message)
        self.centred = centred
        self.summary = summary
        self.squares = squares

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        if self.centred:
            self._draw_centred(d, image_width, clock_format)
            return img
        left, right = MARGIN, image_width - MARGIN
        half = (right - left) / 2
        time, date = clock_and_date(clock_format)
        _fitted(d, (left, 62), time, UBUNTU_BOLD, 50, half, "ls")
        _fitted(d, (left, 88), date, GOOGLE_REGULAR, 16, half, "ls")
        if self.squares is not None:
            _draw_squares(d, image_width / 2, 36, self.squares)
        current = current_page(self.pages)
        if self.summary is not None:
            _fitted(d, (right, 62), self.summary, GOOGLE_REGULAR, 18, half - 40, "rs")
        elif current is not None:
            width = _temperature(d, (right, 62), current, 50, space=half - 80, right=True)
            _icon(d, current.weather_code, (right - width - 36, 40), 56, is_day=current.is_day)
            line = f"{_condition(current)}  ·  {current.high:.0f}° / {current.low:.0f}°"
            _fitted(d, (right, 88), line, GOOGLE_REGULAR, 16, half - 40, "rs")
        elif self.message is not None:
            _fitted(d, (right, 62), _(self.message[0]), GOOGLE_SEMI, 18, half - 40, "rs")
            _fitted(d, (right, 88), _(self.message[1]), GOOGLE_REGULAR, 15, half - 40, "rs")
        return img

    def _draw_centred(self, d: ScaledDraw, width: int, clock_format: ClockFormat) -> None:
        """Draw the time big in the middle, the date under it, and the weather now on a line under that."""
        middle = width / 2
        time, date = clock_and_date(clock_format)
        _fitted(d, (middle, 124), time, UBUNTU_BOLD, 118, width - 2 * MARGIN, "ms")
        _fitted(d, (middle, 160), date, GOOGLE_REGULAR, 20, width - 2 * MARGIN, "ms")
        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                line = f"{_(self.message[0])}: {_(self.message[1])}"
                _fitted(d, (middle, 196), line, GOOGLE_REGULAR, 18, width - 2 * MARGIN, "ms")
            return
        line = f"{round(current.temperature)}°  {_condition(current)}   ·   {high_low(current)}"
        font = _font(GOOGLE_REGULAR, 18)
        icon_room = 46
        line_width = min(font.getlength(line), width - 2 * MARGIN - icon_room)
        x = middle - (icon_room + line_width) / 2
        _icon(d, current.weather_code, (x + 18, 189), 34, is_day=current.is_day)
        _fitted(d, (x + icon_room, 196), line, GOOGLE_REGULAR, 18, line_width, "ls")


def _draw_squares(d: ScaledDraw, centre: float, y: float, squares: tuple[int, int], fill: Fill = "black") -> None:
    """Draw a square per page, centred on `centre`, filled for the one on screen."""
    page, pages = squares
    x = centre - (pages * (SQUARE + SQUARE_GAP) - SQUARE_GAP) / 2
    for i in range(pages):
        box = (x, y, x + SQUARE - 1, y + SQUARE - 1)
        if i == page:
            d.rectangle(box, fill=fill)
        else:
            d.rectangle(box, outline=fill, width=2)
        x += SQUARE + SQUARE_GAP
