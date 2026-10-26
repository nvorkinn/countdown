"""The weather on the HD portrait screen, where it spans the whole width: four designs, picked on the config page.

The config's weather.design says which (models.WeatherDesign). Each is laid out in pixels sized for the 7.5" screens,
553 x 264, and the layout draws it at the stops' scale (rendering.layouts.hd_portrait), so its text is their size.
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.panel import Panel
from countdown_renderer.utils.clock import format_clock
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, METEOCONS, resized
from countdown_renderer.weather.weather_panel import (
    _caption,
    _condition,
    _draw_tabs,
    _temperature_text,
    _weekend_dates,
    icon_for,
)
from countdown_renderer.weather.weather_trmnl_panel import page_outlook, page_readings
from countdown_shared.i18n import _, format_date
from countdown_shared.weather.models import Weather, WeatherDesign, WeatherHour, WeatherPeriod

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from PIL import Image, ImageFont

    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import Fill, ScaledDraw

# Text sizes, in the panel's pixels: a small-caps label (the stops' small text), a reading, and a line of conditions.
LABEL = 11
VALUE = 17
BODY = 18
# The strip the timeline's hours go in starts here.
STRIP_TOP = 166
# The poster's black band starts here.
BAND_TOP = 216
# The room a cell's icon takes in the poster's band.
ICON_ROOM = 40

Point = tuple[float, float]
# A cell after the readings: its label, its text, and the icon's weather code (None for no icon) and whether by day.
Outlook = tuple[str, str, int | None, bool]


class HdWeatherPanel(Panel):
    """The page of the weather carousel the HD screen shows, in the config's design."""

    scales = True

    def __init__(self, weather: Weather, design: WeatherDesign = "columns", pages: Sequence[Weather] = ()) -> None:
        """`weather` is the page's readings; `pages` every page's, which the overview draws side by side."""
        super().__init__()
        self.weather = weather
        self.design = design
        self.pages = tuple(pages) or (weather,)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, draw = scaled_canvas(image_width, image_height)
        DESIGNS[self.design](draw, self, image_width, image_height, clock_format)
        return img


def _columns(d: ScaledDraw, panel: HdWeatherPanel, width: int, height: int, clock_format: ClockFormat) -> None:  # noqa: ARG001 -- every design is called alike
    """Draw the conditions big on the left, and the readings in tiles on the right with the outlook under them."""
    w = panel.weather
    left = 298  # the conditions' column; the tabs only have it, so they're a little smaller
    _draw_tabs(d, w.period, (0, 4), left, 0.86)
    _text(d, (0, 47), _caption(w), GOOGLE_SEMI, LABEL)
    temperature_width = _temperature(d, (0, 146), w, 82, space=left - 110)
    _icon(d, w.weather_code, (temperature_width + 64, 116), 96, is_day=w.is_day)
    _text(d, (0, 170), _condition(w), GOOGLE_REGULAR, BODY + 1)
    readings = page_readings(w, clock_format)
    for i, (label, value) in enumerate(_summary(w, readings)):
        _stat(d, (i * 76, 202), label, value, (LABEL, VALUE + 4), 72)

    x0 = 304
    tile_width, tile_height, gap = (width - x0 - 10) // 2, 62, 10
    for i, (label, value) in enumerate(readings[:4]):
        x, y = x0 + (i % 2) * (tile_width + gap), 4 + (i // 2) * (tile_height + gap)
        d.rounded_rectangle((x, y, x + tile_width, y + tile_height), 8, outline="black", width=1)
        _stat(d, (x + 10, y + 10), label, value, (LABEL, VALUE + 3), tile_width - 20)
    y = 4 + 2 * (tile_height + gap) + 4
    d.line((x0, y, width, y), fill="black", width=1)
    cells = _outlook(w, clock_format)
    cell = (width - x0) / max(1, len(cells))
    for i, (label, text, code, is_day) in enumerate(cells):
        x = x0 + i * cell
        _stat(d, (x, y + 18), label, text, (LABEL, VALUE + 5), cell - 84)
        if code is not None:
            _icon(d, code, (x + cell - 37, y + 44), 72, is_day=is_day)


def _timeline(d: ScaledDraw, panel: HdWeatherPanel, width: int, height: int, clock_format: ClockFormat) -> None:
    """Draw the conditions and readings on top, and the coming hours in a strip along the bottom."""
    w = panel.weather
    _draw_tabs(d, w.period, (0, 4), width, 1.0)
    _text(d, (0, 45), _caption(w), GOOGLE_SEMI, LABEL)
    temperature_width = _temperature(d, (0, 124), w, 72, space=200)
    _icon(d, w.weather_code, (temperature_width + 54, 100), 84, is_day=w.is_day)
    _text(d, (0, 144), _condition(w), GOOGLE_REGULAR, BODY)
    column = 112
    for i, (label, value) in enumerate(page_readings(w, clock_format)[:4]):
        at = (width - 2 * column + (i % 2) * column, 45 + (i // 2) * 52)
        _stat(d, at, label, value, (LABEL, VALUE + 4), column - 8)

    draw_hours(d, w.hours, STRIP_TOP, (width, height), clock_format)


def _overview(d: ScaledDraw, panel: HdWeatherPanel, width: int, height: int, clock_format: ClockFormat) -> None:
    """Every page at once, so nothing rotates: now big on the left, then tonight, tomorrow and the weekend."""
    pages = {page.period: page for page in panel.pages}
    now = pages.get(WeatherPeriod.NOW, panel.weather)
    _text(d, (0, 6), _(WeatherPeriod.NOW.value).upper(), GOOGLE_SEMI, LABEL + 2)
    if now.forecast_date is not None:
        _fitted(d, (0, 24), format_date(now.forecast_date, _("EEEE d MMMM")).upper(), GOOGLE_SEMI, LABEL - 1, 112)
    _icon(d, now.weather_code, (156, 62), 76, is_day=now.is_day)
    _temperature(d, (0, 118), now, 80, space=110)
    _text(d, (0, 141), _condition(now), GOOGLE_REGULAR, BODY)
    for i, (label, value) in enumerate(page_readings(now, clock_format)[:4]):
        _stat(d, ((i % 2) * 96, 172 + (i // 2) * 44), label, value, (LABEL - 1, VALUE + 1), 92)
    d.line((200, 8, 200, height - 8), fill="black", width=1)

    x, gap = 214, 10
    column_width = (width - x - 2 * gap) / 3
    for i, period in enumerate((WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW, WeatherPeriod.WEEKEND)):
        left = x + i * (column_width + gap)
        if i:
            d.line((left - gap / 2 - 2, 8, left - gap / 2 - 2, height - 8), fill="black", width=1)
        _text(d, (left, 6), _(period.value).upper(), GOOGLE_SEMI, LABEL + 2)
        page = pages.get(period)
        if page is None:
            continue
        _fitted(d, (left, 24), _subtitle(page, clock_format), GOOGLE_SEMI, LABEL - 1, column_width)
        _icon(d, page.weather_code, (left + column_width / 2, 76), 64, is_day=page.is_day)
        _temperature(d, (left, 156), page, 48, space=column_width)
        for j, (label, value) in enumerate(_column_stats(page, clock_format)):
            _stat(d, (left, 172 + j * 44), label, value, (LABEL - 1, VALUE + 1), column_width)


def _poster(d: ScaledDraw, panel: HdWeatherPanel, width: int, height: int, clock_format: ClockFormat) -> None:
    """Draw the temperature very big and heavy, the icon too, and the readings in white on a black band."""
    w = panel.weather
    _draw_tabs(d, w.period, (0, 4), width, 1.0)
    _text(d, (0, 45), _caption(w), GOOGLE_SEMI, LABEL)
    _temperature(d, (0, 148), w, 112, space=width - 200, base=GOOGLE_SEMI)
    _text(d, (2, 180), _condition(w), GOOGLE_SEMI, LABEL * 2)
    _icon(d, w.weather_code, (width - 96, 108), 168, is_day=w.is_day)
    d.rounded_rectangle((0, BAND_TOP, width - 1, height - 1), 10, fill="black")
    cells: list[Outlook] = [(label, value, None, True) for label, value in page_readings(w, clock_format)]
    cells += _outlook(w, clock_format)
    # Each cell's share of the band is the room its texts want (the wider of its label and its value) and its icon's.
    label_font, value_font = _font(GOOGLE_SEMI, LABEL - 1), _font(GOOGLE_REGULAR, VALUE + 1)
    wanted = [
        max(label_font.getlength(label.upper()), value_font.getlength(text)) + 24 + _icon_room(code)
        for label, text, code, _is_day in cells
    ]
    x = 0.0
    for (label, text, code, is_day), want in zip(cells, wanted, strict=True):
        cell = want * width / sum(wanted)
        _stat(d, (x + 12, BAND_TOP + 9), label, text, (LABEL - 1, VALUE + 1), cell - 24 - _icon_room(code), "white")
        if code is not None:
            _icon(d, code, (x + cell - 30, BAND_TOP + 24), 31, is_day=is_day, fill="white")
        x += cell


def draw_hours(
    d: ScaledDraw, hours: Sequence[WeatherHour], top: float, size: tuple[float, float], clock_format: ClockFormat
) -> None:
    """Draw a rule at `top`, and under it the coming `hours` side by side, across `size` (the panel's width and height).

    Each hour has its time, its weather's icon, its temperature and its chance of rain, with a hairline between two.
    """
    width, height = size
    d.line((0, top, width, top), fill="black", width=1)
    if not hours:
        return
    slot = width / len(hours)
    for i, hour in enumerate(hours):
        centre = slot * (i + 0.5)
        _text(d, (centre, top + 8), format_clock(hour.time, clock_format), GOOGLE_SEMI, LABEL, "mt")
        _icon(d, hour.weather_code, (centre, top + 40), 36, is_day=hour.is_day)
        _text(d, (centre, top + 76), f"{round(hour.temperature)}°", GOOGLE_REGULAR, BODY + 1, "ms")
        if hour.precipitation_probability is not None:
            rain = _("{percent}% rain").format(percent=hour.precipitation_probability)
            _text(d, (centre, top + 88), rain, GOOGLE_SEMI, LABEL - 1, "ms")
        if i:
            d.line((slot * i, top + 12, slot * i, height - 8), fill="black", width=1)


DESIGNS: dict[WeatherDesign, Callable[[ScaledDraw, HdWeatherPanel, int, int, ClockFormat], None]] = {
    "columns": _columns,
    "timeline": _timeline,
    "overview": _overview,
    "poster": _poster,
}


def _summary(w: Weather, readings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return the compact panel's readings (high, low and rain) that the page's own row doesn't already have."""
    taken = {label for label, _value in readings}
    stats = [(_("High"), f"{w.high:.0f}°"), (_("Low"), f"{w.low:.0f}°")]
    if w.precipitation_probability is not None:
        stats.append((_("Rain"), f"{w.precipitation_probability}%"))
    return [stat for stat in stats if stat[0] not in taken]


def _outlook(w: Weather, clock_format: ClockFormat) -> list[Outlook]:
    """Return what follows the page's readings, in cells: the next part of the day, with its weather, for now.

    For tonight its hours, and for tomorrow its sunrise (the date is in the caption, and its sunset a reading);
    for the weekend its two days. The portrait weather's footer (page_outlook) says the same in one long line.
    """
    if w.period is WeatherPeriod.NOW:
        return [page_outlook(w, clock_format)]
    if w.period is WeatherPeriod.TONIGHT and w.starts_at is not None and w.ends_at is not None:
        hours = f"{format_clock(w.starts_at, clock_format)} - {format_clock(w.ends_at, clock_format)}"
        return [(_("OVERNIGHT"), hours, None, False)]
    if w.period is WeatherPeriod.TOMORROW:
        return [(_("Sunrise"), format_clock(w.sunrise, clock_format), None, True)]
    return [
        (format_date(day.date, "EEEE"), f"{round(day.high)}° / {round(day.low)}°", day.weather_code, True)
        for day in w.days
    ]


def _icon_room(code: int | None) -> int:
    """Return how much of a poster band cell its icon takes: nothing without one."""
    return ICON_ROOM if code is not None else 0


def _subtitle(page: Weather, clock_format: ClockFormat) -> str:
    """Return what the overview says under a column's title: tonight's hours, or the date or dates."""
    if page.period is WeatherPeriod.WEEKEND:
        return _weekend_dates(page).upper()
    if page.period is WeatherPeriod.TONIGHT:
        if page.starts_at is None or page.ends_at is None:
            return ""
        return f"{format_clock(page.starts_at, clock_format)} - {format_clock(page.ends_at, clock_format)}"
    if page.forecast_date is None:
        return ""
    return format_date(page.forecast_date, _("EEEE d MMMM")).upper()


def _column_stats(page: Weather, clock_format: ClockFormat) -> list[tuple[str, str]]:
    """Return the two readings under a column's temperature: each weekend day's, or the page's first two."""
    if page.period is WeatherPeriod.WEEKEND:
        return [(format_date(day.date, "EEEE"), f"{round(day.high)}° / {round(day.low)}°") for day in page.days]
    return page_readings(page, clock_format)[:2]


def _font(base: ImageFont.FreeTypeFont, size: float) -> ImageFont.FreeTypeFont:
    return resized(base, max(1, round(size)))


def _text(  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, in which font and colour
    d: ScaledDraw,
    xy: Point,
    text: str,
    base: ImageFont.FreeTypeFont,
    size: float,
    anchor: str = "la",
    fill: Fill = "black",
) -> None:
    d.text(xy, text, fill, _font(base, size), anchor)


def _fitted(  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, and how much room it has
    d: ScaledDraw,
    xy: Point,
    text: str,
    base: ImageFont.FreeTypeFont,
    size: float,
    space: float,
    anchor: str = "la",
    fill: Fill = "black",
) -> None:
    """Draw `text` in `base` at `size`, or smaller until it fits `space`."""
    font = _font(base, size)
    while font.getlength(text) > space and font.size > 1:
        font = resized(font, int(font.size) - 1)
    d.text(xy, text, fill, font, anchor)


def _icon(  # noqa: PLR0913 -- a drawing helper: which icon, where, how big and in which colour
    d: ScaledDraw, code: int, centre: Point, size: float, *, is_day: bool, fill: Fill = "black"
) -> None:
    """Centre the weather icon's ink on `centre`: the font's own box (anchor mm) isn't where these icons sit."""
    glyph, font = icon_for(code, is_day=is_day), _font(METEOCONS, size)
    left, top, right, bottom = font.getbbox(glyph)
    d.text((centre[0] - (left + right) / 2, centre[1] - (top + bottom) / 2), glyph, fill, font)


def _temperature(  # noqa: PLR0913, PLR0917 -- a drawing helper: whose temperature, where, how big, font, colour
    d: ScaledDraw,
    xy: Point,
    w: Weather,
    size: int,
    space: float | None = None,
    base: ImageFont.FreeTypeFont = GOOGLE_REGULAR,
    fill: Fill = "black",
    *,
    right: bool = False,
) -> float:
    """Draw the page's temperature on its baseline at `xy`, the unit small beside it, both shrunk to fit `space`.

    `xy` is where the pair starts, or with `right` where it ends. Returns how wide the pair is.
    """
    number, units = _temperature_text(w), w.temperature_units
    while True:
        number_font, unit_font = _font(base, size), _font(base, size * 0.38)
        gap = max(1, round(size * 0.06))
        number_width = number_font.getlength(number)
        width = number_width + gap + unit_font.getlength(units)
        if space is None or width <= space or size <= 1:
            break
        size -= 1
    x = xy[0] - width if right else xy[0]
    d.text((x, xy[1]), number, fill, number_font, "ls")
    d.text((x + number_width + gap, xy[1]), units, fill, unit_font, "ls")
    return width


def _stat(  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, how big, and in which colour
    d: ScaledDraw,
    xy: Point,
    label: str,
    value: str,
    sizes: tuple[float, float],
    space: float,
    fill: Fill = "black",
) -> None:
    """Draw a small-caps `label` with the bigger `value` under it, from `xy` (the label's top left), fitting `space`."""
    label_size, value_size = sizes
    _fitted(d, xy, label.upper(), GOOGLE_SEMI, label_size, space, fill=fill)
    _fitted(d, (xy[0], xy[1] + label_size * 1.3 + value_size), value, GOOGLE_REGULAR, value_size, space, "ls", fill)
