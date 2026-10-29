"""The weather in brief, for the kerbside layouts, so it reads from across the room.

A big icon and the temperature, the conditions, and the day's high, low and chance of rain; nothing else. Laid out in
pixels sized for the 7.5" screens, centred in its width; the HD screen's kerbside layouts draw it bigger.
"""

from typing import TYPE_CHECKING, override

from countdown_renderer.panel import Panel
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_REGULAR, METEOCONS, google_symbols, resized
from countdown_renderer.weather.weather_panel import _condition, icon_for

if TYPE_CHECKING:
    from PIL import Image, ImageFont

    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import ScaledDraw
    from countdown_shared.weather.models import Weather

# How tall the panel is laid out, in its own pixels.
HEIGHT = 250
# The icon's size, the gap between it and the temperature, and the temperature's size.
ICON = 136
ICON_GAP = 20
TEMPERATURE_SIZE = 122
# The conditions under them: their size and baseline.
CONDITION_SIZE = 34
CONDITION_BASELINE = 194
# The high, low and chance of rain on one line: each one's icon and value, the gap between them, and their baseline.
READING_SIZE = 40
READING_GAP = 34
READINGS_BASELINE = 248
# Material Symbols glyphs: arrow_upward, arrow_downward and water_drop.
HIGH, LOW, RAIN = chr(0xE5D8), chr(0xE5DB), chr(0xE798)


class KerbsideWeatherPanel(Panel):
    """The weather now, in brief: the icon and the temperature, the conditions, then the high, low and rain."""

    scales = True

    def __init__(self, weather: Weather) -> None:
        """`weather` is what's shown: the now page's readings."""
        super().__init__()
        self.weather = weather

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        w = self.weather
        middle = image_width / 2

        # The icon and the temperature side by side, centred together.
        glyph, glyph_font = icon_for(w.weather_code, is_day=w.is_day), resized(METEOCONS, ICON)
        left, top, right, bottom = glyph_font.getbbox(glyph)
        temperature, temperature_font = f"{round(w.temperature)}°", resized(GOOGLE_REGULAR, TEMPERATURE_SIZE)
        glyph_width = right - left
        x = middle - (glyph_width + ICON_GAP + temperature_font.getlength(temperature)) / 2
        d.text((x - left, ICON * 0.62 - (top + bottom) / 2), glyph, "black", glyph_font)
        d.text((x + glyph_width + ICON_GAP, ICON * 0.98), temperature, "black", temperature_font, anchor="ls")

        d.text((middle, CONDITION_BASELINE), _condition(w), "black", resized(GOOGLE_REGULAR, CONDITION_SIZE), "ms")
        self._draw_readings(d, middle)
        return img

    def _draw_readings(self, d: ScaledDraw, middle: float) -> None:
        """Draw the high, low and (if forecast) chance of rain on one line, each after its icon, centred together."""
        w = self.weather
        readings = [(HIGH, f"{round(w.high)}°"), (LOW, f"{round(w.low)}°")]
        if w.precipitation_probability is not None:
            readings.append((RAIN, f"{w.precipitation_probability}%"))
        value_font, icon_font = resized(GOOGLE_REGULAR, READING_SIZE), resized(google_symbols(), READING_SIZE)
        widths = [self._reading_width(icon, value, icon_font, value_font) for icon, value in readings]
        x = middle - (sum(widths) + READING_GAP * (len(readings) - 1)) / 2
        for (icon, value), width in zip(readings, widths, strict=True):
            d.text((x, READINGS_BASELINE - READING_SIZE * 0.35), icon, "black", icon_font, anchor="lm")
            d.text((x + icon_font.getlength(icon) + 4, READINGS_BASELINE), value, "black", value_font, anchor="ls")
            x += width + READING_GAP

    @staticmethod
    def _reading_width(
        icon: str, value: str, icon_font: ImageFont.FreeTypeFont, value_font: ImageFont.FreeTypeFont
    ) -> float:
        return icon_font.getlength(icon) + 4 + value_font.getlength(value)
