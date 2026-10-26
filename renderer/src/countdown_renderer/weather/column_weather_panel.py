"""The weather column of the HD landscape column layout: a weather poster with the time over it and the hours under it.

A line with the date and the time; the weather now big (its icon, the temperature, the conditions, the day's high and
low); under a rule, the coming hours a row each; and at the foot, as many of the forecast's later pages (tonight,
tomorrow, the weekend) as leave room for four hours. It shows the render's moment (core.moment) and every page at once,
so nothing in it rotates. Laid out in pixels sized for the 7.5" screens; the layout draws it twice as big.
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.utils.clock import format_clock
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, UBUNTU_BOLD
from countdown_renderer.weather.hd_clock_panels import HdWeatherColumnPanel, clock_and_date, high_low
from countdown_renderer.weather.hd_sidebar_panel import current_page, later_rows
from countdown_renderer.weather.hd_weather_panel import _fitted, _font, _icon, _temperature
from countdown_renderer.weather.weather_panel import _condition
from countdown_shared.i18n import _

if TYPE_CHECKING:
    from PIL import Image

    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import ScaledDraw
    from countdown_renderer.weather.hd_sidebar_panel import Row
    from countdown_shared.weather.models import WeatherHour

# Where the hours' rule is, how tall an hour's row is, and a later page's; the hours the later pages leave room for.
RULE = 392
HOUR_STEP = 31
PAGE_STEP = 40
HOURS_KEPT = 4


class ColumnWeatherPanel(HdWeatherColumnPanel):
    """The date and the time on a line, the weather now as a poster, the coming hours, and the later pages."""

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        width = image_width
        time, date = clock_and_date(clock_format)
        time_font = _font(UBUNTU_BOLD, 30)
        time_width = min(time_font.getlength(time), width / 2)
        _fitted(d, (width, 28), time, UBUNTU_BOLD, 30, time_width, "rs")
        _fitted(d, (0, 28), date.upper(), GOOGLE_SEMI, 14, width - time_width - 12, "ls")

        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                title, text = self.message
                _fitted(d, (0, 80), _(title), GOOGLE_SEMI, 20, width, "ls")
                _fitted(d, (0, 106), _(text), GOOGLE_REGULAR, 15, width, "ls")
            return img

        _icon(d, current.weather_code, (width * 0.4, 124), 160, is_day=current.is_day)
        _temperature(d, (0, 308), current, 120, space=width)
        _fitted(d, (0, 348), _condition(current), GOOGLE_REGULAR, 26, width, "ls")
        _fitted(d, (0, 376), high_low(current), GOOGLE_REGULAR, 17, width, "ls")

        rows = later_rows(self.pages, clock_format)
        hours = current.hours
        while rows and RULE + len(hours[:HOURS_KEPT]) * HOUR_STEP + len(rows) * PAGE_STEP + 12 > image_height:
            rows = rows[:-1]
        pages_top = image_height - len(rows) * PAGE_STEP
        if hours:
            d.line((0, RULE, width, RULE), fill="black", width=2)
        y = RULE + 6
        for hour in hours:
            if y + HOUR_STEP > pages_top - 6:
                break
            _draw_hour(d, hour, (y + HOUR_STEP / 2, width), clock_format)
            y += HOUR_STEP
        _draw_later(d, rows, pages_top, width)
        return img


def _draw_hour(d: ScaledDraw, hour: WeatherHour, row: tuple[float, int], clock_format: ClockFormat) -> None:
    """Draw an hour's row, its middle at `row`'s height, across its width: the time, the icon, rain, temperature."""
    middle, width = row
    _fitted(d, (0, middle), format_clock(hour.time, clock_format), GOOGLE_REGULAR, 16, 80, "lm")
    _icon(d, hour.weather_code, (width * 0.45, middle), 28, is_day=hour.is_day)
    if hour.precipitation_probability is not None:
        _fitted(d, (width * 0.72, middle), f"{hour.precipitation_probability}%", GOOGLE_REGULAR, 13, 44, "rm")
    d.text((width, middle), f"{round(hour.temperature)}°", "black", _font(GOOGLE_SEMI, 18), "rm")


def _draw_later(d: ScaledDraw, rows: list[Row], top: int, width: int) -> None:
    """Draw the later pages' rows from `top`, under a rule: each one's title, its conditions, and its icon."""
    if not rows:
        return
    d.line((0, top - 6, width, top - 6), fill="black", width=1)
    y = top
    for title, detail, code, is_day in rows:
        _fitted(d, (0, y + 4), title, GOOGLE_SEMI, 11, width - 44, "la")
        _fitted(d, (0, y + 31), detail, GOOGLE_REGULAR, 14, width - 44, "ls")
        _icon(d, code, (width - 16, y + 17), 28, is_day=is_day)
        y += PAGE_STEP
