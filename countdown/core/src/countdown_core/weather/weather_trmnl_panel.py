from PIL import Image, ImageDraw, ImageFont

from countdown_core.core.panel import Panel
from countdown_core.utils.utils import GAP, METEOCONS, UBUNTU_BOLD, UBUNTU_MEDIUM, UBUNTU_REGULAR, google_symbols
from countdown_core.weather.models import Upcoming, Weather
from countdown_core.weather.weather_panel import _draw_centered, icon_for

# Material Symbols glyphs
SUNRISE = chr(0xE1C6)
SUNSET = chr(0xFFF1F)
HUMIDITY = chr(0xF879)
WIND = chr(0xEFD8)

# How tall the panel draws itself: the details column is laid out at fixed heights.
HEIGHT = 270


class WeatherTrmnlPanel(Panel):
    """The weather in full, for a portrait screen: a big icon on the left; on the right the
    temperature, sunrise and sunset, humidity and wind, and the outlook for later."""

    def __init__(self, weather: Weather):
        super().__init__()
        self.weather = weather

    def render(self, image_width: int, image_height: int) -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))

        icon_section_width = int((image_width - GAP) * 0.45)
        temp_section_width = int((image_width - GAP) * 0.55)

        icon_section = self._render_icon_section(icon_section_width, image_height)
        img.paste(icon_section, (0, 0), icon_section)

        temp_section = self._render_temp_section(temp_section_width, image_height)
        img.paste(temp_section, (icon_section_width + GAP, 0), temp_section)
        return img

    def _render_icon_section(self, section_width: int, section_height: int) -> Image.Image:
        img = Image.new("RGBA", (section_width, section_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        # As big as the space allows.
        icon_size = min(section_width, section_height)
        _draw_centered(
            draw,
            icon_for(self.weather.weather_code, self.weather.is_day),
            (section_width / 2, section_height / 2),
            METEOCONS.font_variant(size=icon_size),
        )
        return img

    def _render_temp_section(self, section_width: int, section_height: int) -> Image.Image:
        img = Image.new("RGBA", (section_width, section_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        w = self.weather
        symbols = google_symbols().font_variant(size=40)
        detail = UBUNTU_MEDIUM.font_variant(size=20)

        text = f"{round(w.temperature)}{w.temperature_units}"
        draw.text((section_width / 2, 0), text, "black", UBUNTU_REGULAR.font_variant(size=110), "ma")

        draw.text((GAP, 120), SUNRISE, "black", symbols)
        draw.text((50, 135), w.sunrise.isoformat("minutes"), "black", detail)
        draw.text((120, 120), SUNSET, "black", symbols)
        draw.text((165, 135), w.sunset.isoformat("minutes"), "black", detail)

        draw.text((GAP, 160), HUMIDITY, "black", symbols)
        draw.text((50, 175), f"{w.humidity}{w.humidity_units}", "black", detail)
        draw.text((120, 160), WIND, "black", symbols)
        wind = f"{round(w.wind_speed)}{w.wind_speed_units} {_compass(w.wind_direction)}"
        _draw_shrunk_to_fit(draw, (165, 175), wind, UBUNTU_MEDIUM, section_width - 165, 20)

        upcoming, code = w.upcoming_weather_code
        _draw_centered(
            draw, icon_for(code, upcoming is not Upcoming.TONIGHT), (35, 240), METEOCONS.font_variant(size=60)
        )
        _draw_shrunk_to_fit(draw, (75, 225), WEATHER_CODE_DESC.get(code, ""), UBUNTU_BOLD, section_width - 75, 20)
        draw.text((75, 245), str(upcoming), "black", UBUNTU_MEDIUM.font_variant(size=18))
        return img


COMPASS_8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _compass(deg: float) -> str:
    return COMPASS_8[round(deg / 45) % 8]


def _draw_shrunk_to_fit(
    draw: ImageDraw.ImageDraw,
    coord: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    space: int,
    optimal_size: int,
) -> None:
    """`text` at `optimal_size`, or smaller if that's wider than `space`."""
    font = font.font_variant(size=optimal_size)
    while font.getlength(text) > space and font.size > 1:
        font = font.font_variant(size=font.size - 1)
    draw.text(coord, text, "black", font=font)


# What Open-Meteo's WMO weather codes mean.
WEATHER_CODE_DESC = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}
