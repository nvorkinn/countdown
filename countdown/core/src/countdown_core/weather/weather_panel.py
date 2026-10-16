from PIL import Image, ImageDraw, ImageFont

from countdown_core.core.panel import Panel
from countdown_core.i18n import N_, _, format_date
from countdown_core.spotify.spotify_panel import truncate_to_fit
from countdown_core.utils.clock import ClockFormat
from countdown_core.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, METEOCONS
from countdown_core.weather.models import Weather, WeatherPeriod

FALLBACK_ICON = ")"  # "N/A"


class WeatherPanel(Panel):
    def __init__(self, weather: Weather):
        super().__init__()
        self.weather = weather

    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        weather = self.weather

        if image_height < 140 and image_width >= image_height * 3.5:
            self._render_inline(draw, image_width, image_height)
            return img

        scale = min(image_width / 241, image_height / 185, 1.25)
        width = min(image_width - 32 * scale, 310 * scale)
        x = (image_width - width) / 2
        y = (image_height - 185 * scale) / 2
        _draw_tabs(draw, weather.period, (x, y + 11 * scale), width, scale)
        _draw_shrunk_to_fit(draw, (x, y + 44 * scale), _caption(weather), GOOGLE_SEMI, width, round(9 * scale))

        icon_size = round(min(80 * scale, width * 0.28))
        _draw_temperature(
            draw,
            (x, y + 101 * scale),
            _temperature_text(weather, decimals=1),
            weather.temperature_units,
            width - icon_size - 18 * scale,
            round(52 * scale),
        )
        _draw_centered(
            draw,
            icon_for(weather.weather_code, weather.is_day),
            (x + width - icon_size / 2, y + 80 * scale),
            METEOCONS.font_variant(size=max(1, icon_size)),
        )
        _draw_shrunk_to_fit(
            draw,
            (image_width / 2, y + 124 * scale),
            _condition(weather),
            GOOGLE_REGULAR,
            width,
            round(13 * scale),
            anchor="mm",
        )

        self._draw_readings(draw, (x, y + 140 * scale), width, scale)
        return img

    def _render_inline(self, draw: ImageDraw.ImageDraw, image_width: int, image_height: int) -> None:
        # A single bus stop crops the top row down to a short, wide strip.
        scale = min(image_width / 528, image_height / 76, 1.25)
        x = 12 * scale
        y = (image_height - 76 * scale) / 2
        width = image_width - 2 * x
        current_width = width * 0.49
        weather = self.weather
        _draw_tabs(draw, weather.period, (x, y + 5 * scale), width, scale)
        icon_size = round(31 * scale)
        _draw_temperature(
            draw,
            (x, y + 64 * scale),
            _temperature_text(weather, decimals=1),
            weather.temperature_units,
            119 * scale,
            round(34 * scale),
        )
        _draw_centered(
            draw,
            icon_for(weather.weather_code, weather.is_day),
            (x + current_width - icon_size / 2, y + 53 * scale),
            METEOCONS.font_variant(size=max(1, icon_size)),
        )
        detail_font = GOOGLE_REGULAR.font_variant(size=max(1, round(11 * scale)))
        draw.text(
            (x + 128 * scale, y + 57 * scale),
            truncate_to_fit(_condition(weather), detail_font, current_width - icon_size - 142 * scale),
            "black",
            font=detail_font,
            anchor="lm",
        )
        gutter = 28 * scale
        self._draw_readings(draw, (x + current_width + gutter, y + 35 * scale), width - current_width - gutter, scale)

    def _draw_readings(self, draw: ImageDraw.ImageDraw, coord: tuple[float, float], width: float, scale: float) -> None:
        weather = self.weather
        readings = [(_("LOW"), f"{weather.low:.0f}°"), (_("HIGH"), f"{weather.high:.0f}°")]
        if weather.precipitation_probability is not None:
            readings.append((_("RAIN"), f"{weather.precipitation_probability}%"))
        x, y = coord
        label = GOOGLE_SEMI.font_variant(size=max(1, round(11 * scale)))
        column_width = width / len(readings)
        for index, (title, value) in enumerate(readings):
            center = x + column_width * (index + 0.5)
            draw.text((center, y), title, "black", font=label, anchor="mt")
            _draw_shrunk_to_fit(
                draw,
                (center, y + 29 * scale),
                value,
                GOOGLE_REGULAR,
                column_width - 8 * scale,
                round(18 * scale),
                anchor="ms",
            )


def _draw_tabs(
    draw: ImageDraw.ImageDraw,
    selected: WeatherPeriod,
    coord: tuple[float, float],
    width: float,
    scale: float,
) -> None:
    labels = [_(period.value).upper() for period in WeatherPeriod]
    font = GOOGLE_SEMI.font_variant(size=max(1, round(11 * scale)))
    padding, gap, height = 13 * scale, 8 * scale, 25 * scale
    if sum(font.getlength(label) + 2 * padding for label in labels) + 3 * gap > width:
        # Short forms of the tabs, for a narrow panel.
        labels = [_("NOW"), _("NIGHT"), _("TMRW"), _("WKND")]
        font = GOOGLE_SEMI.font_variant(size=max(1, round(10 * scale)))
        padding, gap, height = 7 * scale, 4 * scale, 21 * scale
    x, y = coord
    for period, label in zip(WeatherPeriod, labels, strict=True):
        tab_width = font.getlength(label) + 2 * padding
        active = period is selected
        if active:
            draw.rounded_rectangle((x, y, x + tab_width, y + height), radius=height / 2, fill="black")
        draw.text(
            (x + tab_width / 2, y + height / 2),
            label,
            "white" if active else "black",
            font=font,
            anchor="mm",
        )
        x += tab_width + gap


def _caption(weather: Weather) -> str:
    if weather.period is WeatherPeriod.WEEKEND:
        if len(weather.days) != 2:
            raise ValueError("The weekend page needs Saturday and Sunday readings")
        first, last = weather.days
        # Translators: a CLDR date pattern, e.g. "17 Oct". Norwegian: "d. MMM".
        dates = (
            f"{first.date.day}-{format_date(last.date, _('d MMM'))}"
            if first.date.month == last.date.month
            else f"{format_date(first.date, _('d MMM'))} - {format_date(last.date, _('d MMM'))}"
        )
        return _("DAYTIME HIGHS / {dates}").format(dates=dates.upper())
    return _(
        {
            WeatherPeriod.NOW: N_("CURRENT CONDITIONS"),
            WeatherPeriod.TONIGHT: N_("OVERNIGHT LOW"),
            WeatherPeriod.TOMORROW: N_("DAYTIME HIGH"),
        }[weather.period]
    )


def _temperature_text(weather: Weather, decimals: int = 0) -> str:
    if weather.period is WeatherPeriod.WEEKEND:
        if len(weather.days) != 2:
            raise ValueError("The weekend page needs Saturday and Sunday readings")
        low, high = min(round(day.high) for day in weather.days), max(round(day.high) for day in weather.days)
        return f"{low}–{high}" if low != high else str(high)
    if weather.period is WeatherPeriod.NOW and decimals:
        return f"{weather.temperature:.{decimals}f}"
    return str(round(weather.temperature))


def _condition(weather: Weather) -> str:
    if weather.period is WeatherPeriod.WEEKEND and len({day.weather_code for day in weather.days}) > 1:
        return _("Mixed skies")
    return _(WEATHER_CODE_DESC.get(weather.weather_code, N_("Unknown conditions")))


def _draw_temperature(
    draw: ImageDraw.ImageDraw,
    coord: tuple[float, float],
    temperature: str,
    units: str,
    space: float,
    optimal_size: int,
) -> None:
    """Keep the unit secondary, on the number's baseline, and fit the pair together."""
    size = max(1, optimal_size)
    while True:
        number_font = GOOGLE_REGULAR.font_variant(size=size)
        unit_font = GOOGLE_REGULAR.font_variant(size=max(1, round(size * 0.38)))
        gap = max(1, round(size * 0.06))
        number_width = number_font.getlength(temperature)
        if number_width + gap + unit_font.getlength(units) <= space or size == 1:
            break
        size -= 1
    draw.text(coord, temperature, "black", font=number_font, anchor="ls")
    draw.text((coord[0] + number_width + gap, coord[1]), units, "black", font=unit_font, anchor="ls")


def _draw_shrunk_to_fit(
    draw: ImageDraw.ImageDraw,
    coord: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    space: float,
    optimal_size: int,
    anchor: str = "lt",
) -> None:
    font = font.font_variant(size=max(1, optimal_size))
    while font.getlength(text) > space and font.size > 1:
        font = font.font_variant(size=font.size - 1)
    draw.text(coord, text, "black", font=font, anchor=anchor)


def icon_for(weather_code: int, is_day: bool) -> str:
    day_icon, night_icon = ICON_MAP.get(weather_code, (FALLBACK_ICON, FALLBACK_ICON))
    return day_icon if is_day else night_icon


def _draw_centered(draw: ImageDraw.ImageDraw, glyph: str, center: tuple[float, float], font: ImageFont.FreeTypeFont):
    """Centre the glyph's actual ink on `center` -- the font's own ascender/descender
    box (what anchor="mm" uses) isn't where these icons visually sit."""
    left, top, right, bottom = draw.textbbox((0, 0), glyph, font=font)
    draw.text(
        (center[0] - (left + right) / 2, center[1] - (top + bottom) / 2),
        glyph,
        "black",
        font=font,
    )


# Open-Meteo reports WMO weather interpretation codes; each maps to a (day, night) pair
# of Meteocons glyphs. Only clear/mainly-clear/partly-cloudy differ between the two.
_SUN_MOON = ("B", "C")
_PARTLY_CLOUDY = ("H", "I")
ICON_MAP: dict[int, tuple[str, str]] = {
    0: _SUN_MOON,  # clear sky
    1: _SUN_MOON,  # mainly clear
    2: _PARTLY_CLOUDY,
    3: ("Y", "Y"),  # overcast
    45: ("L", "L"),  # fog
    48: ("L", "L"),  # rime fog
    51: ("Q", "Q"),  # drizzle: light, moderate, dense
    53: ("Q", "Q"),
    55: ("Q", "Q"),
    56: ("X", "X"),  # freezing drizzle
    57: ("X", "X"),
    61: ("Q", "Q"),  # rain: slight, moderate, heavy
    63: ("R", "R"),
    65: ("R", "R"),
    66: ("X", "X"),  # freezing rain
    67: ("X", "X"),
    71: ("U", "U"),  # snow fall: slight, moderate, heavy
    73: ("V", "V"),
    75: ("W", "W"),
    77: ("X", "X"),  # snow grains
    80: ("Q", "Q"),  # rain showers: slight, moderate, violent
    81: ("R", "R"),
    82: ("R", "R"),
    85: ("V", "V"),  # snow showers: slight, heavy
    86: ("W", "W"),
    95: ("O", "O"),  # thunderstorm
    96: ("P", "P"),  # thunderstorm with slight/heavy hail
    99: ("P", "P"),
}

# What Open-Meteo's WMO weather codes mean.
WEATHER_CODE_DESC = {
    0: N_("Clear sky"),
    1: N_("Mainly clear"),
    2: N_("Partly cloudy"),
    3: N_("Overcast"),
    45: N_("Fog"),
    48: N_("Depositing rime fog"),
    51: N_("Light drizzle"),
    53: N_("Moderate drizzle"),
    55: N_("Dense drizzle"),
    56: N_("Light freezing drizzle"),
    57: N_("Dense freezing drizzle"),
    61: N_("Slight rain"),
    63: N_("Moderate rain"),
    65: N_("Heavy rain"),
    66: N_("Light freezing rain"),
    67: N_("Heavy freezing rain"),
    71: N_("Slight snowfall"),
    73: N_("Moderate snowfall"),
    75: N_("Heavy snowfall"),
    77: N_("Snow grains"),
    80: N_("Slight rain showers"),
    81: N_("Moderate rain showers"),
    82: N_("Violent rain showers"),
    85: N_("Slight snow showers"),
    86: N_("Heavy snow showers"),
    95: N_("Thunderstorm"),
    96: N_("Thunderstorm with slight hail"),
    99: N_("Thunderstorm with heavy hail"),
}
