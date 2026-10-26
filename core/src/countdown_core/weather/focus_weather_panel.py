"""The time and the weather as the HD landscape screen's focus layout draws them, in the column on its right.

The time with the date across from it; the weather now big, its temperature with its icon across from it, and a line
of its condition, the day's high and low and the chance of rain; the coming hours side by side; then, with room to
spare, the forecast's later pages (tonight, tomorrow, the weekend) a row each. All at once, so nothing rotates. Laid
out in pixels sized for the 7.5" screens; the layout draws it twice as big (rendering.layouts.hd_landscape_focus).
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, UBUNTU_BOLD, fitted_font
from countdown_core.weather.hd_clock_panels import clock_and_date
from countdown_core.weather.hd_sidebar_panel import current_page, later_rows
from countdown_core.weather.hd_weather_panel import _fitted, _icon, draw_hours
from countdown_core.weather.weather_panel import _condition, _temperature_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw
    from countdown_core.weather.models import Weather, WeatherHour

# The most coming hours side by side, and the strip they're in (under the weather now): where it starts, how tall.
HOURS = 6
HOURS_TOP = 196
HOURS_HEIGHT = 96
# Each later page's row under the hours, and the gap over them.
PAGE_STEP = 44
PAGES_GAP = 8
# How tall it is without a forecast: the time alone, and with a message under it.
CLOCK_HEIGHT = 56
MESSAGE_HEIGHT = 150
# The temperature's size, and its baseline.
TEMPERATURE_SIZE = 100
TEMPERATURE_BASELINE = 152


class FocusWeatherPanel(Panel):
    """The time and date, the weather now big, the coming hours, and the later pages if there's room."""

    scales = True

    def __init__(
        self,
        pages: Sequence[Weather] = (),
        message: tuple[str, str] | None = None,
        hours: Sequence[WeatherHour] = (),
    ) -> None:
        """`pages`: the weather carousel's (now first), none if there's no forecast; `message`: then, what to say.

        `message` is a (title, text) pair, as a MessagePanel has them, both already marked for translation. `hours`:
        the forecast's coming hours (the first few are drawn).
        """
        super().__init__()
        self.pages = tuple(pages)
        self.message = message
        self.hours = tuple(hours)[:HOURS]

    @override
    def fitted_height(self, max_height: int) -> int:
        """How tall it is with as many of the later pages as fit in `max_height`: the room past them is left over.

        Without a forecast, the time and what the message says; with more than `max_height` needed for the weather now
        and the hours, all of `max_height`.
        """
        if current_page(self.pages) is None:
            return min(max_height, MESSAGE_HEIGHT if self.message is not None else CLOCK_HEIGHT)
        height = HOURS_TOP + (HOURS_HEIGHT if self.hours else 0)
        if max_height < height:
            return max_height
        rows = len(later_rows(self.pages, "24h"))
        fitting = min(rows, max(0, (max_height - height - PAGES_GAP) // PAGE_STEP))
        return height + (PAGES_GAP + fitting * PAGE_STEP if fitting else 0)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        time, date = clock_and_date(clock_format)
        time_font = fitted_font("Ubuntu-Bold.ttf", 44)
        _fitted(d, (0, 40), time, UBUNTU_BOLD, 44, image_width / 2, "ls")
        time_width = min(time_font.getlength(time), image_width / 2)
        _fitted(d, (image_width, 32), date, GOOGLE_REGULAR, 17, image_width - time_width - 16, "rs")

        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                title, text = self.message
                _fitted(d, (0, 100), _(title), GOOGLE_SEMI, 22, image_width, "ls")
                _fitted(d, (0, 128), _(text), GOOGLE_REGULAR, 17, image_width, "ls")
            return img

        self._draw_now(d, current, image_width)
        y = HOURS_TOP
        if self.hours and y + HOURS_HEIGHT <= image_height:
            draw_hours(d, self.hours, y, (image_width, y + HOURS_HEIGHT), clock_format)
            y += HOURS_HEIGHT
        rows = later_rows(self.pages, clock_format)
        if not rows or y + PAGE_STEP > image_height:
            return img
        d.line((0, y, image_width, y), fill="black", width=1)
        y += PAGES_GAP
        for title, detail, code, is_day in rows:
            if y + PAGE_STEP > image_height:
                break
            _fitted(d, (0, y + 4), title, GOOGLE_SEMI, 12, image_width - 50, "la")
            _fitted(d, (0, y + 34), detail, GOOGLE_REGULAR, 17, image_width - 50, "ls")
            _icon(d, code, (image_width - 22, y + 20), 32, is_day=is_day)
            y += PAGE_STEP
        return img

    @staticmethod
    def _draw_now(d: ScaledDraw, w: Weather, width: int) -> None:
        """Draw the temperature big with its icon across from it, and under them a line of the rest."""
        number = _temperature_text(w)
        number_font = fitted_font("Ubuntu-Medium.ttf", TEMPERATURE_SIZE)
        d.text((-4, TEMPERATURE_BASELINE), number, "black", number_font, anchor="ls")
        unit_font = fitted_font("Ubuntu-Regular.ttf", 28)
        cap_height = TEMPERATURE_SIZE * 0.7 - 28 * 0.7
        d.text(
            (number_font.getlength(number) + 4, TEMPERATURE_BASELINE - cap_height),
            w.temperature_units,
            "black",
            unit_font,
            anchor="ls",
        )
        _icon(d, w.weather_code, (width - 62, 108), 124, is_day=w.is_day)
        parts = [_condition(w), f"\N{UPWARDS ARROW}{w.high:.0f}° \N{DOWNWARDS ARROW}{w.low:.0f}°"]
        if w.precipitation_probability is not None:
            parts.append(_("{percent}% rain").format(percent=w.precipitation_probability))
        _fitted(d, (0, 182), "  \N{MIDDLE DOT}  ".join(parts), GOOGLE_REGULAR, 19, width, "ls")
