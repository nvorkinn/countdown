from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, METEOCONS, google_symbols
from countdown_core.weather.models import Upcoming, Weather
from countdown_core.weather.weather_panel import (
    WEATHER_CODE_DESC,
    _draw_centered,
    _draw_shrunk_to_fit,
    _draw_temperature,
    icon_for,
)

# Material Symbols glyphs
SUNRISE = chr(0xE1C6)
SUNSET = chr(0xFFF1F)
HUMIDITY = chr(0xF879)
WIND = chr(0xEFD8)

HEIGHT = 270


class WeatherTrmnlPanel(Panel):
    """Current conditions, a quiet row of readings, and the next outlook on portrait screens."""

    def __init__(self, weather: Weather):
        super().__init__()
        self.weather = weather

    def render(self, image_width: int, image_height: int) -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        w = self.weather
        scale = min(image_width / 470, image_height / HEIGHT)
        x = (image_width - 470 * scale) / 2 + 20 * scale
        y = (image_height - HEIGHT * scale) / 2
        width = 430 * scale
        label = GOOGLE_SEMI.font_variant(size=max(1, round(11 * scale)))
        draw.text((x, y + 16 * scale), "NOW", "black", font=label, anchor="lt")
        _draw_temperature(
            draw,
            (x, y + 103 * scale),
            str(round(w.temperature)),
            w.temperature_units,
            280 * scale,
            round(88 * scale),
        )
        _draw_centered(
            draw,
            icon_for(w.weather_code, w.is_day),
            (x + width - 65 * scale, y + 75 * scale),
            METEOCONS.font_variant(size=max(1, round(106 * scale))),
        )
        _draw_shrunk_to_fit(
            draw,
            (x, y + 124 * scale),
            WEATHER_CODE_DESC.get(w.weather_code, "Unknown conditions"),
            GOOGLE_REGULAR,
            width,
            round(16 * scale),
            anchor="lm",
        )

        symbols = google_symbols().font_variant(size=max(1, round(20 * scale)))
        readings = [
            (SUNRISE, "Sunrise", w.sunrise.isoformat("minutes")),
            (SUNSET, "Sunset", w.sunset.isoformat("minutes")),
            (HUMIDITY, "Humidity", f"{w.humidity}{w.humidity_units}"),
            (WIND, "Wind", f"{round(w.wind_speed)} {w.wind_speed_units} {_compass(w.wind_direction)}"),
        ]
        column_width = width / len(readings)
        for index, (symbol, title, value) in enumerate(readings):
            left = x + column_width * index
            _draw_centered(draw, symbol, (left + 10 * scale, y + 151 * scale), symbols)
            draw.text((left + 25 * scale, y + 151 * scale), title, "black", font=label, anchor="lm")
            _draw_shrunk_to_fit(
                draw,
                (left, y + 183 * scale),
                value,
                GOOGLE_REGULAR,
                column_width - 12 * scale,
                round(19 * scale),
                anchor="ls",
            )

        draw.line((x, y + 202 * scale, x + width, y + 202 * scale), fill="black")
        upcoming, code = w.upcoming_weather_code
        draw.text((x, y + 217 * scale), str(upcoming).upper(), "black", font=label, anchor="lt")
        _draw_shrunk_to_fit(
            draw,
            (x, y + 245 * scale),
            WEATHER_CODE_DESC.get(code, "Unknown conditions"),
            GOOGLE_REGULAR,
            width - 65 * scale,
            round(19 * scale),
            anchor="ls",
        )
        _draw_centered(
            draw,
            icon_for(code, upcoming is not Upcoming.TONIGHT),
            (x + width - 22 * scale, y + 234 * scale),
            METEOCONS.font_variant(size=max(1, round(44 * scale))),
        )
        return img


COMPASS_8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _compass(deg: float) -> str:
    return COMPASS_8[round(deg / 45) % 8]
