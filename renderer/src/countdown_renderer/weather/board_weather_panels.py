"""The clock and the weather as the HD landscape screen's departure board draws them (layouts.hd_landscape_board).

Two panels: a slim line across the top, with the time and the date on the left and the weather now in brief on the
right; and the weather now, big, at the start of the strip under the stops. Both show the weather carousel's page for
now, so nothing in them rotates. They're laid out in pixels sized for the 7.5" screens, and the layout draws them twice
as big.
"""

from typing import TYPE_CHECKING, override

from countdown_core.i18n import _
from countdown_renderer.panel import Panel
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_REGULAR, GOOGLE_SEMI, UBUNTU_BOLD
from countdown_renderer.weather.hd_clock_panels import SQUARE, SQUARE_GAP, _draw_squares, clock_and_date, high_low
from countdown_renderer.weather.hd_sidebar_panel import current_page
from countdown_renderer.weather.hd_weather_panel import _fitted, _font, _icon, _temperature
from countdown_renderer.weather.weather_panel import _condition

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from countdown_core.weather.models import Weather
    from countdown_renderer.utils.clock import ClockFormat

# The header line's height, and the room at its ends.
HEADER_HEIGHT = 46
MARGIN = 20
# Where the header's text sits: the baseline the clock, the date and the weather share.
BASELINE = 34
# The weather's icon in the header: its size, and the room it takes before the line it's beside.
ICON_SIZE = 40
ICON_ROOM = 50
# Between the date and the weather, at the least.
GAP = 30


class _WeatherNow(Panel):
    """What both panels share: the carousel's pages, or what to say without them."""

    scales = True

    def __init__(self, pages: Sequence[Weather] = (), message: tuple[str, str] | None = None) -> None:
        """`pages`: the weather carousel's (now first), none if there's no forecast; `message`: then, what to say.

        `message` is a (title, text) pair, as a MessagePanel has them, both already marked for translation.
        """
        super().__init__()
        self.pages = tuple(pages)
        self.message = message


class BoardHeaderPanel(_WeatherNow):
    """The time and date on the left, and the weather now in brief on the right, on one line.

    Over the middle, the squares say which page of the stops is on screen, when they don't all fit (`squares`: (the
    page, how many)). Without a forecast the line has only the time: the weather panel under the stops says why.
    """

    def __init__(
        self,
        pages: Sequence[Weather] = (),
        message: tuple[str, str] | None = None,
        squares: tuple[int, int] | None = None,
    ) -> None:
        """See _WeatherNow; `squares`, if given, are drawn in the middle."""
        super().__init__(pages, message)
        self.squares = squares

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        right = image_width - MARGIN
        time, date = clock_and_date(clock_format)
        clock_font, date_font = _font(UBUNTU_BOLD, 32), _font(GOOGLE_REGULAR, 18)
        d.text((MARGIN, BASELINE), time, "black", clock_font, "ls")
        date_x = MARGIN + clock_font.getlength(time) + 24
        d.text((date_x, BASELINE), date, "black", date_font, "ls")
        if self.squares is not None:
            _draw_squares(d, image_width / 2, BASELINE - SQUARE, self.squares)

        current = current_page(self.pages)
        if current is None:
            return img
        left = date_x + date_font.getlength(date) + GAP
        if self.squares is not None:
            left = max(left, image_width / 2 + self.squares[1] * (SQUARE + SQUARE_GAP) / 2 + GAP)
        line = f"{round(current.temperature)}°  {_condition(current)}   ·   {high_low(current)}"
        font = _font(GOOGLE_REGULAR, 18)
        while font.getlength(line) > right - left - ICON_ROOM and font.size > 1:
            font = _font(GOOGLE_REGULAR, font.size - 1)
        d.text((right, BASELINE), line, "black", font, "rs")
        icon_x = right - font.getlength(line) - ICON_ROOM / 2 - 4
        _icon(d, current.weather_code, (icon_x, BASELINE - 6), ICON_SIZE, is_day=current.is_day)
        return img


class BoardWeatherPanel(_WeatherNow):
    """The weather now: its temperature big beside its icon, its conditions, and the day's high, low and rain."""

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        d.text((0, 0), _("Weather").upper(), "black", _font(GOOGLE_SEMI, 13), "la")
        current = current_page(self.pages)
        if current is None:
            if self.message is not None:
                # Under the label, the message's title (the weather's name) would say it twice.
                _title, text = self.message
                _fitted(d, (0, 64), _(text), GOOGLE_SEMI, 20, image_width, "ls")
            return img
        icon = min(92, image_width * 0.4)
        _temperature(d, (0, 106), current, 82, space=image_width - icon - 8)
        _icon(d, current.weather_code, (image_width - icon / 2, 72), icon, is_day=current.is_day)
        _fitted(d, (0, 132), _condition(current), GOOGLE_REGULAR, 19, image_width, "ls")
        _fitted(d, (0, 157), _stats(current), GOOGLE_REGULAR, 15, image_width, "ls")
        return img


def _stats(w: Weather) -> str:
    """Return the day's high and low, and the chance of rain if known, e.g. "High 18°   Low 9°   Rain 40%"."""
    stats = [f"{_('High')} {w.high:.0f}°", f"{_('Low')} {w.low:.0f}°"]
    if w.precipitation_probability is not None:
        stats.append(f"{_('Rain')} {w.precipitation_probability}%")
    return "   ".join(stats)
