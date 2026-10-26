"""The weather on the HD screen hung landscape: a black sidebar down its left edge, and the coming hours in a strip.

The sidebar has the time and the date over the weather now, white on black, with the rest of the carousel's pages
(tonight, tomorrow and the weekend) a row each under it, so nothing in it rotates. The strip is the timeline design's
(hd_weather_panel.draw_hours), across the stops' width. Both are laid out in pixels sized for the 7.5" screens, and the
layout draws them twice as big (rendering.layouts.hd_landscape).
"""

from typing import TYPE_CHECKING, override

from countdown_core.core import moment
from countdown_core.core.panel import Panel
from countdown_core.i18n import _, format_date, local_zone
from countdown_core.utils.clock import format_clock
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, UBUNTU_BOLD
from countdown_core.weather.hd_weather_panel import _fitted, _icon, _temperature, _text, draw_hours
from countdown_core.weather.models import WeatherPeriod
from countdown_core.weather.weather_panel import _condition, _temperature_text
from countdown_core.weather.weather_trmnl_panel import page_outlook, page_readings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw
    from countdown_core.weather.models import Weather, WeatherHour

# Between the sidebar's edges and what's in it, in its own pixels.
MARGIN = 22
# Where the readings' rows start, how far apart they are, and where the other pages' rows start and how tall each is.
READINGS_TOP = 432
READING_STEP = 26
PAGES_TOP = 542
PAGE_STEP = 50
# The rows under the current conditions: the forecast's pages after now, in the carousel's order.
LATER = (WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW, WeatherPeriod.WEEKEND)
# A later page's row: (its title, a line of conditions, its icon's weather code, whether by day).
Row = tuple[str, str, int, bool]


class HdSidebarPanel(Panel):
    """The time and date, and the weather now with every later page under it, white on black."""

    scales = True

    def __init__(self, pages: Sequence[Weather] = (), message: tuple[str, str] | None = None) -> None:
        """`pages`: the weather carousel's (now first), none if there's no forecast; `message`: then, what to say.

        `message` is a (title, text) pair, as a MessagePanel has them, both already marked for translation.
        """
        super().__init__()
        self.pages = tuple(pages)
        self.message = message

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height, (0, 0, 0, 255))
        left, right = MARGIN, image_width - MARGIN
        space = right - left
        now = moment.now().astimezone(local_zone())
        _fitted(d, (left, 80), format_clock(now, clock_format), UBUNTU_BOLD, 72, space, "ls", "white")
        _fitted(d, (left, 110), format_date(now.date(), _("EEEE d MMMM")), GOOGLE_REGULAR, 18, space, "ls", "white")
        d.line((left, 132, right, 132), fill="white", width=1)

        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                title, text = self.message
                _fitted(d, (left, 180), _(title), GOOGLE_SEMI, 20, space, "ls", "white")
                _fitted(d, (left, 208), _(text), GOOGLE_REGULAR, 16, space, "ls", "white")
            return img

        _icon(d, current.weather_code, (left + 62, 205), 120, is_day=current.is_day, fill="white")
        _temperature(d, (left, 360), current, 96, space=space, fill="white")
        _fitted(d, (left, 394), _condition(current), GOOGLE_REGULAR, 22, space, "ls", "white")
        _draw_readings(d, current, (left, right), clock_format)

        rows = later_rows(self.pages, clock_format)
        if rows:
            d.line((left, PAGES_TOP - 14, right, PAGES_TOP - 14), fill="white", width=1)
        y = PAGES_TOP
        for title, detail, code, is_day in rows:
            _fitted(d, (left, y + 12), title, GOOGLE_SEMI, 12, space - 50, "ls", "white")
            _fitted(d, (left, y + 33), detail, GOOGLE_REGULAR, 16, space - 50, "ls", "white")
            _icon(d, code, (right - 20, y + 18), 34, is_day=is_day, fill="white")
            y += PAGE_STEP
        return img


def _draw_readings(d: ScaledDraw, w: Weather, span: tuple[float, float], clock_format: ClockFormat) -> None:
    """Draw the page's readings a row each, the label on the left and the value against the right edge."""
    left, right = span
    for i, (label, value) in enumerate(readings(w, clock_format)):
        y = READINGS_TOP + i * READING_STEP
        _text(d, (left, y), label.upper(), GOOGLE_SEMI, 12, "ls", "white")
        _fitted(d, (right, y), value, GOOGLE_REGULAR, 17, (right - left) / 2, "rs", "white")


def current_page(pages: Sequence[Weather]) -> Weather | None:
    """Return the page with the weather now (else the first), or None without a forecast."""
    return next((page for page in pages if page.period is WeatherPeriod.NOW), pages[0] if pages else None)


def readings(w: Weather, clock_format: ClockFormat) -> list[tuple[str, str]]:
    """Return the readings the HD screen's weather shows beside the current conditions, (label, value) each.

    The day's high and low on one row, then the chance of rain; of the page's own readings, the sunset and the wind.
    """
    found = [(_("High / Low"), f"{w.high:.0f}° / {w.low:.0f}°")]
    if w.precipitation_probability is not None:
        found.append((_("Rain"), f"{w.precipitation_probability}%"))
    return found + [reading for i, reading in enumerate(page_readings(w, clock_format)) if i in {1, 3}]


def later_rows(pages: Sequence[Weather], clock_format: ClockFormat) -> list[Row]:
    """Return a row for each of the forecast's pages after now, in the carousel's order.

    A lone page (no forecast's other pages) has what it says comes next instead.
    """
    by_period = {page.period: page for page in pages}
    rows = [_row(page) for period in LATER if (page := by_period.get(period)) is not None]
    current = current_page(pages)
    if not rows and current is not None and current.period is WeatherPeriod.NOW:
        title, detail, code, is_day = page_outlook(current, clock_format)
        rows = [(title, detail, code, is_day)]
    return rows


def _row(page: Weather) -> Row:
    """Return a later page's row: its tab's title with its temperature, and its conditions."""
    title = f"{_(page.period.value).upper()}  {_temperature_text(page)}°"
    return title, _condition(page), page.weather_code, page.is_day


class HdHoursPanel(Panel):
    """The coming hours side by side, under a rule: the timeline design's strip, on its own."""

    scales = True

    def __init__(self, hours: Sequence[WeatherHour]) -> None:
        """`hours`: the forecast's coming hours, as each weather page has them (Weather.hours)."""
        super().__init__()
        self.hours = tuple(hours)

    @override
    def fitted_height(self, max_height: int) -> int:
        """Nothing without hours to show (a weather page that isn't from a forecast)."""
        return max_height if self.hours else 0

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        draw_hours(d, self.hours, 0, (image_width, image_height), clock_format)
        return img
