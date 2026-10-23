"""The weather big, as the HD landscape pages layout's home page shows it (rendering.layouts.hd_landscape_pages).

The weather now: its icon and its temperature very big, its conditions with the day's high and low and the chance of
rain on a line under them, the coming hours, its readings (sunrise, sunset, humidity, wind), and each of the forecast's
later pages (tonight, tomorrow, the weekend) a row each. In a column (beside the energy chart) they're one under the
other; with the room of the whole screen, the later pages go beside the weather now and the hours under both.
Laid out in pixels sized for the 7.5" screens, and drawn bigger by the layout.
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI
from countdown_core.weather.hd_sidebar_panel import HdHoursPanel, current_page, later_rows
from countdown_core.weather.hd_weather_panel import _fitted, _icon, _stat, _temperature, draw_hours
from countdown_core.weather.weather_panel import _condition
from countdown_core.weather.weather_trmnl_panel import page_readings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw
    from countdown_core.weather.models import Weather

# Wider than this, the later pages go beside the weather now rather than under it; then the weather now has this
# much of the width, and the hours go under both if it's this tall.
WIDE_FROM = 640
NOW_WIDTH = 430
WIDE_HEIGHT = 430
# The weather now: the icon's size and the temperature's, and where the temperature's baseline and the line of
# conditions under it are.
ICON_SIZE = 180
TEMPERATURE_SIZE = 150
TEMPERATURE_BASELINE = 168
CONDITIONS_BASELINE = 222
# The coming hours' strip: how tall it is, and the most hours it shows.
HOURS_HEIGHT = 100
MOST_HOURS = 8
# The readings' row: its height (a label over a value) and their sizes.
READINGS_HEIGHT = 50
READING_SIZES = (12, 20)
# A later page's row, and the gap between the parts.
ROW_STEP = 54
GAP = 18


class HdPagesWeatherPanel(Panel):
    """The weather now big, the coming hours, its readings and the forecast's later pages."""

    scales = True

    def __init__(self, pages: Sequence[Weather]) -> None:
        """`pages`: the weather carousel's, now first (see layouts.hd.weather_parts); at least one."""
        super().__init__()
        self.pages = tuple(pages)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        current = current_page(self.pages)
        if current is None:
            return img
        if image_width >= WIDE_FROM:
            self._draw_wide(d, current, (image_width, image_height), clock_format)
        else:
            self._draw_column(d, current, (image_width, image_height), clock_format)
        return img

    def _draw_column(self, d: ScaledDraw, w: Weather, size: tuple[int, int], clock_format: ClockFormat) -> None:
        """Draw it all one under the other: now, the hours, the readings, and the later pages while they fit."""
        width, height = size
        _draw_now(d, w, width)
        y = CONDITIONS_BASELINE + GAP
        if w.hours:
            draw_hours(d, w.hours[:MOST_HOURS], y, (width, y + HOURS_HEIGHT), clock_format)
            y += HOURS_HEIGHT + GAP
        else:
            d.line((0, y, width, y), fill="black", width=1)
            y += GAP
        _draw_readings(d, w, (0, y), width, clock_format)
        y += READINGS_HEIGHT + GAP
        self._draw_later(d, (0, y), (width, height), clock_format, rule=True)

    def _draw_wide(self, d: ScaledDraw, w: Weather, size: tuple[int, int], clock_format: ClockFormat) -> None:
        """Draw now and its readings on the left, the later pages beside them, and the hours under both.

        Without the room for the hours under both, they go under the later pages.
        """
        width, height = size
        _draw_now(d, w, NOW_WIDTH)
        _draw_readings(d, w, (0, CONDITIONS_BASELINE + GAP), NOW_WIDTH, clock_format)
        rule = NOW_WIDTH + GAP
        left = rule + GAP
        rows_bottom = height
        if w.hours:
            hours = w.hours[:MOST_HOURS]
            if height >= WIDE_HEIGHT:
                top = height - HOURS_HEIGHT
                draw_hours(d, hours, top, (width, height), clock_format)
                rows_bottom = top - GAP
            else:
                rows = len(later_rows(self.pages, clock_format))
                top = min(rows * ROW_STEP + GAP // 2, height - HOURS_HEIGHT)
                strip = HdHoursPanel(hours).render(width - left, HOURS_HEIGHT, clock_format=clock_format)
                d.paste(strip, (left, top))
                rows_bottom = top
        # As tall as the weather now and its readings, beside it.
        d.line((rule, 8, rule, min(rows_bottom, CONDITIONS_BASELINE + GAP + READINGS_HEIGHT)), fill="black", width=1)
        self._draw_later(d, (left, 0), (width, rows_bottom), clock_format)

    def _draw_later(
        self,
        d: ScaledDraw,
        at: tuple[int, int],
        size: tuple[int, int],
        clock_format: ClockFormat,
        *,
        rule: bool = False,
    ) -> None:
        """Draw the later pages a row each from `at`, as many as fit in `size` (the right and bottom edges).

        With `rule`, under a rule across the panel.
        """
        left, y = at
        right, bottom = size
        rows = later_rows(self.pages, clock_format)
        if not rows:
            return
        if rule:
            d.line((0, y, right, y), fill="black", width=1)
            y += GAP // 2
        space = right - left - 60
        for title, detail, code, is_day in rows:
            if y + ROW_STEP > bottom:
                break
            _fitted(d, (left, y + 16), title, GOOGLE_SEMI, 13, space, "ls")
            _fitted(d, (left, y + 42), detail, GOOGLE_REGULAR, 20, space, "ls")
            _icon(d, code, (right - 26, y + ROW_STEP / 2 - 2), 40, is_day=is_day)
            y += ROW_STEP


def _draw_now(d: ScaledDraw, w: Weather, width: int) -> None:
    """Draw the weather now in `width`: its icon and temperature very big, and its conditions on a line under them."""
    _icon(d, w.weather_code, (ICON_SIZE / 2, TEMPERATURE_BASELINE - 66), ICON_SIZE, is_day=w.is_day)
    left = ICON_SIZE + GAP
    _temperature(d, (left, TEMPERATURE_BASELINE), w, TEMPERATURE_SIZE, space=width - left)
    parts = [_condition(w), f"{w.high:.0f}° / {w.low:.0f}°"]
    if w.precipitation_probability is not None:
        parts.append(_("Rain") + f" {w.precipitation_probability}%")
    _fitted(d, (0, CONDITIONS_BASELINE), "   ·   ".join(parts), GOOGLE_REGULAR, 22, width, "ls")


def _draw_readings(d: ScaledDraw, w: Weather, at: tuple[int, int], width: int, clock_format: ClockFormat) -> None:
    """Draw the page's readings side by side from `at`, a small label over each value."""
    x, y = at
    readings = page_readings(w, clock_format)
    cell = width / max(4, len(readings))
    for i, (label, value) in enumerate(readings):
        _stat(d, (x + i * cell, y), label, value, READING_SIZES, cell - 10)
