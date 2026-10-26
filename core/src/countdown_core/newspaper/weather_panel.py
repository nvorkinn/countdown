"""The weather's column on the newspaper's front page: the weather now in a box, its readings, and what comes later.

The box has the icon, the temperature big and the conditions. Under it, the readings as a table with dotted leaders
(the day's high and low, the chance of rain, sunset, the wind, as many as there's room for), then a row for each of the
forecast's later pages (tonight, tomorrow, the weekend) with its icon.
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.i18n import N_, _
from countdown_core.newspaper.typesetting import CONTENT_TOP, headline, serif, serif_bold, shrunk, table_row, wrapped
from countdown_core.utils.clock import format_clock
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import truncate_to_fit
from countdown_core.weather.hd_sidebar_panel import current_page
from countdown_core.weather.hd_weather_panel import _icon
from countdown_core.weather.models import Upcoming, WeatherPeriod
from countdown_core.weather.weather_panel import WEATHER_CODE_DESC, _condition, _temperature_text
from countdown_core.weather.weather_trmnl_panel import _compass

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw
    from countdown_core.weather.models import Weather

# The box with the weather now: how tall, and in it the icon's middle and size, the temperature's baseline and size,
# and the conditions' baseline.
BOX_HEIGHT = 166
ICON_MIDDLE = 44
ICON_SIZE = 78
TEMPERATURE_BASELINE = 128
TEMPERATURE_SIZE = 50
CONDITION_BASELINE = 154
# The readings' table under the box: the room above it, how far apart its rows are, and its type's size.
READINGS_GAP = 26
READING_STEP = 22
READING_SIZE = 14
# The later pages' rows, under a rule: the room either side of it, and how tall each row is.
LATER_GAP = 10
LATER_STEP = 32
LATER_SIZE = 12
# The readings always shown, and how many later rows they leave room for before more readings are added.
FIRST_READINGS = 4
LATER_ROWS = 2
# The forecast's pages after now, in the carousel's order.
LATER = (WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW, WeatherPeriod.WEEKEND)
# A later row: its title, its conditions and temperature (none for what comes next), and its icon's weather code and
# whether by day.
Row = tuple[str, str, str, int, bool]


class NewspaperWeatherPanel(Panel):
    """The weather now in a box, its readings, and a row for each later page; without a forecast, why there's none."""

    scales = True

    def __init__(self, pages: Sequence[Weather] = (), message: tuple[str, str] | None = None) -> None:
        """`pages`: the weather carousel's (now first), none if there's no forecast; `message`: then, what to say.

        As layouts.hd.weather_parts gives them: `message` is a (title, text) pair, both still English.
        """
        super().__init__()
        self.pages = tuple(pages)
        self.message = message

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        headline(d, image_width, _("Weather"))
        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                _title, text = self.message
                font = serif(15)
                for i, line in enumerate(wrapped(_(text), font, image_width, 3)):
                    d.text((0, CONTENT_TOP + 20 + i * 20), line, "black", font, anchor="ls")
            return img

        _draw_box(d, current, image_width)
        rows = later_rows(self.pages)
        readings = weather_readings(current, clock_format)
        top = CONTENT_TOP + BOX_HEIGHT + READINGS_GAP
        later_room = LATER_GAP * 2 + LATER_STEP * min(LATER_ROWS, len(rows)) if rows else 0
        fitting = max(FIRST_READINGS, (image_height - top - later_room) // READING_STEP + 1)
        readings = readings[:fitting]
        fonts = serif(READING_SIZE), serif_bold(READING_SIZE)
        for i, (label, value) in enumerate(readings):
            table_row(d, top + i * READING_STEP, (0, image_width), label, value, fonts)

        y = top + (len(readings) - 1) * READING_STEP + LATER_GAP
        if not rows or y + LATER_GAP + LATER_STEP > image_height:
            return img
        d.rectangle((0, y, image_width - 1, y), fill="black")
        y += LATER_GAP
        for row in rows:
            if y + LATER_STEP > image_height:
                break
            _draw_later(d, row, y, image_width)
            y += LATER_STEP
        return img


def _draw_box(d: ScaledDraw, current: Weather, width: int) -> None:
    """Draw the box with the weather now: the icon, the temperature big under it, and the conditions."""
    middle = width / 2
    d.rectangle((0, CONTENT_TOP, width - 1, CONTENT_TOP + BOX_HEIGHT), outline="black", width=2)
    _icon(d, current.weather_code, (middle, CONTENT_TOP + ICON_MIDDLE), ICON_SIZE, is_day=current.is_day)
    temperature = f"{_temperature_text(current)}°"
    font = shrunk(temperature, serif_bold(TEMPERATURE_SIZE), width - 16)
    d.text((middle, CONTENT_TOP + TEMPERATURE_BASELINE), temperature, "black", font, anchor="ms")
    condition = _condition(current)
    font = shrunk(condition, serif(15), width - 12, 11)
    d.text(
        (middle, CONTENT_TOP + CONDITION_BASELINE),
        truncate_to_fit(condition, font, width - 12),
        "black",
        font,
        anchor="ms",
    )


def _draw_later(d: ScaledDraw, row: Row, y: float, width: int) -> None:
    """Draw a later page's row from `y`: its title over its conditions, its icon against the right edge.

    The conditions are cut short rather than the temperature after them.
    """
    title, condition, temperature, code, is_day = row
    room = width - 34
    bold, regular = serif_bold(LATER_SIZE), serif(LATER_SIZE)
    d.text((0, y + 11), truncate_to_fit(title, bold, room), "black", bold, "ls")
    if temperature:
        temperature = f", {temperature}"
        condition = truncate_to_fit(condition, regular, room - regular.getlength(temperature))
    d.text((0, y + 26), truncate_to_fit(condition + temperature, regular, room), "black", regular, "ls")
    _icon(d, code, (width - 14, y + 14), 26, is_day=is_day)


def weather_readings(w: Weather, clock_format: ClockFormat) -> list[tuple[str, str]]:
    """Return the readings under the box, (label, value) each, the most wanted first.

    The day's high and low, the chance of rain, sunset, then the wind, sunrise and the humidity.
    """
    found = [(_("High"), f"{round(w.high)}°"), (_("Low"), f"{round(w.low)}°")]
    if w.precipitation_probability is not None:
        found.append((_("Rain"), f"{w.precipitation_probability}%"))
    found += [
        (_("Sunset"), format_clock(w.sunset, clock_format)),
        (_("Wind"), f"{round(w.wind_speed)} {w.wind_speed_units} {_compass(w.wind_direction)}"),
        (_("Sunrise"), format_clock(w.sunrise, clock_format)),
        (_("Humidity"), f"{w.humidity}{w.humidity_units}"),
    ]
    return found


def later_rows(pages: Sequence[Weather]) -> list[Row]:
    """Return a row for each of the forecast's pages after now: its title, conditions, temperature and icon.

    A lone page (no forecast's other pages) has what it says comes next instead.
    """
    by_period = {page.period: page for page in pages}
    rows = [_row(page) for period in LATER if (page := by_period.get(period)) is not None]
    current = current_page(pages)
    if not rows and current is not None and current.period is WeatherPeriod.NOW:
        upcoming, code = current.upcoming_weather_code
        detail = _(WEATHER_CODE_DESC.get(code, N_("Unknown conditions")))
        rows = [(_(upcoming), detail, "", code, upcoming is not Upcoming.TONIGHT)]
    return rows


def _row(page: Weather) -> Row:
    """Return a later page's row: tomorrow's with its high and low, the others' with their temperature."""
    if page.period is WeatherPeriod.TOMORROW:
        temperature = f"{round(page.high)}° / {round(page.low)}°"
    else:
        temperature = f"{_temperature_text(page)}°"
    return _(page.period.value), _condition(page), temperature, page.weather_code, page.is_day
