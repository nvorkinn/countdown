"""The weather panel for portrait screens: one page of the forecast, with readings and an outlook."""

from typing import override

from PIL import Image, ImageDraw

from countdown_core.i18n import N_, _, format_date
from countdown_core.weather.models import Upcoming, Weather, WeatherPeriod
from countdown_renderer.panel import Panel
from countdown_renderer.utils.clock import ClockFormat, format_clock
from countdown_renderer.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, METEOCONS, resized
from countdown_renderer.weather.weather_panel import (
    WEATHER_CODE_DESC,
    _caption,
    _condition,
    _draw_centered,
    _draw_shrunk_to_fit,
    _draw_tabs,
    _draw_temperature,
    _temperature_text,
    icon_for,
)

HEIGHT = 270


class WeatherTrmnlPanel(Panel):
    """The selected weather period, a quiet readings row, and an outlook on portrait screens."""

    def __init__(self, weather: Weather) -> None:
        """`weather` is the page's readings."""
        super().__init__()
        self.weather = weather

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        w = self.weather
        # Sized by whichever side is shorter for the 470x270 design; a wider panel spreads the rows across its width.
        scale = min(image_width / 470, image_height / HEIGHT)
        x = 20 * scale
        y = (image_height - HEIGHT * scale) / 2
        width = image_width - 2 * x
        _draw_tabs(draw, w.period, (x, y + 11 * scale), width, scale)
        _draw_shrunk_to_fit(
            draw, (x, y + 46 * scale), _caption(w), GOOGLE_SEMI, space=width, optimal_size=round(10 * scale)
        )
        _draw_temperature(
            draw,
            (x, y + 119 * scale),
            _temperature_text(w),
            w.temperature_units,
            space=280 * scale,
            optimal_size=round(82 * scale),
        )
        _draw_centered(
            draw,
            icon_for(w.weather_code, is_day=w.is_day),
            (x + width - 65 * scale, y + 92 * scale),
            resized(METEOCONS, max(1, round(103 * scale))),
        )
        _draw_shrunk_to_fit(
            draw,
            (x, y + 144 * scale),
            _condition(w),
            GOOGLE_REGULAR,
            space=width,
            optimal_size=round(16 * scale),
            anchor="ls",
        )

        readings = self._readings(clock_format)
        column_width = width / len(readings)
        label = resized(GOOGLE_SEMI, max(1, round(11 * scale)))
        for index, (title, value) in enumerate(readings):
            left = x + column_width * index
            draw.text((left, y + 164 * scale), title, "black", font=label, anchor="lt")
            _draw_shrunk_to_fit(
                draw,
                (left, y + 196 * scale),
                value,
                GOOGLE_REGULAR,
                space=column_width - 12 * scale,
                optimal_size=round(19 * scale),
                anchor="ls",
            )

        draw.line((x, y + 214 * scale, x + width, y + 214 * scale), fill="black")
        self._draw_footer(draw, (x, y), width, scale, clock_format)
        return img

    def _readings(self, clock_format: ClockFormat = "24h") -> list[tuple[str, str]]:
        return page_readings(self.weather, clock_format)

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        coord: tuple[float, float],
        width: float,
        scale: float,
        clock_format: ClockFormat,
    ) -> None:
        w = self.weather
        x, y = coord
        label = resized(GOOGLE_SEMI, max(1, round(10 * scale)))
        if w.period is WeatherPeriod.WEEKEND:
            for index, day in enumerate(w.days):
                left = x + index * width / 2
                draw.text(
                    (left, y + 228 * scale), format_date(day.date, "EEEE").upper(), "black", font=label, anchor="lt"
                )
                _draw_shrunk_to_fit(
                    draw,
                    (left, y + 256 * scale),
                    f"{round(day.high)}° / {round(day.low)}°",
                    GOOGLE_REGULAR,
                    space=width / 2 - 55 * scale,
                    optimal_size=round(18 * scale),
                    anchor="ls",
                )
                _draw_centered(
                    draw,
                    icon_for(day.weather_code, is_day=True),
                    (left + width / 2 - 30 * scale, y + 244 * scale),
                    resized(METEOCONS, max(1, round(35 * scale))),
                )
            return
        title, detail, code, is_day = page_outlook(w, clock_format)
        _draw_shrunk_to_fit(
            draw, (x, y + 228 * scale), title, GOOGLE_SEMI, space=width - 65 * scale, optimal_size=round(10 * scale)
        )
        _draw_shrunk_to_fit(
            draw,
            (x, y + 256 * scale),
            detail,
            GOOGLE_REGULAR,
            space=width - 65 * scale,
            optimal_size=round(18 * scale),
            anchor="ls",
        )
        _draw_centered(
            draw,
            icon_for(code, is_day=is_day),
            (x + width - 22 * scale, y + 244 * scale),
            resized(METEOCONS, max(1, round(36 * scale))),
        )


def page_readings(w: Weather, clock_format: ClockFormat = "24h") -> list[tuple[str, str]]:
    """Return the page's readings row, (label, value) each: sunrise, sunset, humidity and wind for now, and so on."""
    wind = (_("Wind"), f"{round(w.wind_speed)} {w.wind_speed_units} {_compass(w.wind_direction)}")
    if w.period is WeatherPeriod.NOW:
        return [
            (_("Sunrise"), format_clock(w.sunrise, clock_format)),
            (_("Sunset"), format_clock(w.sunset, clock_format)),
            (_("Humidity"), f"{w.humidity}{w.humidity_units}"),
            wind,
        ]
    readings = []
    if w.period is WeatherPeriod.TONIGHT:
        if w.feels_like is not None:
            readings.append((_("Feels like"), f"{round(w.feels_like)}°"))
    else:
        readings.append((_("Lowest") if w.period is WeatherPeriod.WEEKEND else _("Low"), f"{round(w.low)}°"))
    if w.precipitation_probability is not None:
        readings.append((_("Rain"), f"{w.precipitation_probability}%"))
    readings.append(wind)
    if w.period is WeatherPeriod.TONIGHT:
        readings.append((_("Humidity"), f"{w.humidity}{w.humidity_units}"))
    elif w.period is WeatherPeriod.TOMORROW:
        readings.append((_("Sunset"), format_clock(w.sunset, clock_format)))
    return readings


def page_outlook(w: Weather, clock_format: ClockFormat = "24h") -> tuple[str, str, int, bool]:
    """Return the page's footer: its title, a detail line, and the icon's weather code and whether by day.

    Now: the next part of the day and its weather. Tonight: the date, overnight, and the hours. Tomorrow: the date,
    and its sunrise and sunset. Not the weekend, whose footer is its two days (WeatherTrmnlPanel._draw_footer).
    """
    if w.period is WeatherPeriod.NOW:
        upcoming, code = w.upcoming_weather_code
        title = _(upcoming).upper()
        detail = _(WEATHER_CODE_DESC.get(code, N_("Unknown conditions")))
        return title, detail, code, upcoming is not Upcoming.TONIGHT
    if w.forecast_date is None:
        msg = "Forecast pages require a forecast date"
        raise ValueError(msg)
    date = w.forecast_date
    # Translators: a CLDR date pattern, e.g. "Friday 16 October". Norwegian: "EEEE d. MMMM".
    title = format_date(date, _("EEEE d MMMM")).upper()
    if w.period is WeatherPeriod.TONIGHT:
        if w.starts_at is None or w.ends_at is None:
            msg = "Tonight requires an overnight time window"
            raise ValueError(msg)
        title += " - " + _("OVERNIGHT")
        detail = f"{format_clock(w.starts_at, clock_format)} - {format_clock(w.ends_at, clock_format)}"
        return title, detail, 0, False
    detail = _("Sunrise {sunrise} / Sunset {sunset}").format(
        sunrise=format_clock(w.sunrise, clock_format), sunset=format_clock(w.sunset, clock_format)
    )
    return title, detail, 0, True


# Translators: compass points, for the wind direction (Norwegian: Ø for east, V for west).
COMPASS_8 = (N_("N"), N_("NE"), N_("E"), N_("SE"), N_("S"), N_("SW"), N_("W"), N_("NW"))


def _compass(deg: float) -> str:
    return _(COMPASS_8[round(deg / 45) % 8])
