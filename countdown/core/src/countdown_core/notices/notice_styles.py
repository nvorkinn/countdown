"""The looks the notice board can have (the config's notice_board.style).

airy, hero and card match the Spotify styles of the same name; boxed is card's square-cornered cousin. A style only
draws the chrome round the rows (the title, the count, any frame) and says how much room that takes; the panel draws
the rows inside it, the same in every style.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from countdown_core.i18n import _, ngettext
from countdown_core.tfl.arrival_styles import BOLD, MEDIUM, SANS_SEMI
from countdown_core.utils.utils import fitted_font, google_symbols

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL import ImageDraw

NoticeStyle = Literal["airy", "hero", "card", "boxed"]

CAMPAIGN = chr(0xEF49)  # Material Symbols "campaign", a megaphone
CARD_BAR = 28  # as tall as Spotify card's title bar
BOXED_BAR = 20  # as tall as the stops' black header bar


def _count(n: int) -> str:
    return ngettext("{n} notice", "{n} notices", n).format(n=n)


def _airy(d: ImageDraw.ImageDraw, width: int, _height: int, count: int) -> None:
    """Draw a small-caps label and the count over a hairline, like Spotify airy's NOW PLAYING."""
    label = fitted_font(SANS_SEMI, 11)
    d.text((0, 8), _("NOTICES"), "black", label, anchor="lm")
    d.text((width - 1, 8), _count(count).upper(), "black", label, anchor="rm")
    d.line((0, 19, width, 19), fill="black")


def _hero(d: ImageDraw.ImageDraw, width: int, _height: int, count: int) -> None:
    """Draw an icon, the title and the count over a 2px rule, like Spotify hero's header."""
    d.text((0, 11), CAMPAIGN, "black", google_symbols().font_variant(size=22), anchor="lm")
    d.text((26, 11), _("Notices"), "black", fitted_font(SANS_SEMI, 17), anchor="lm")
    d.text((width - 1, 11), _count(count), "black", fitted_font(MEDIUM, 13), anchor="rm")
    d.line((0, 26, width, 26), fill="black", width=2)


def _card(d: ImageDraw.ImageDraw, width: int, height: int, count: int) -> None:
    """Draw a rounded card under a black title bar, like Spotify card."""
    d.rounded_rectangle((0, 0, width - 1, height - 1), 10, fill="white", outline="black", width=2)
    d.rounded_rectangle((0, 0, width - 1, CARD_BAR), 10, fill="black")
    d.rectangle((0, CARD_BAR - 10, width - 1, CARD_BAR), fill="black")  # square off the bar's bottom corners
    d.text((10, 14), CAMPAIGN, "white", google_symbols().font_variant(size=18), anchor="lm")
    d.text((32, 14), _("Notices"), "white", fitted_font(SANS_SEMI, 15), anchor="lm")
    d.text((width - 11, 14), _count(count), "white", fitted_font(SANS_SEMI, 13), anchor="rm")


def _boxed(d: ImageDraw.ImageDraw, width: int, height: int, count: int) -> None:
    """Draw a square box under a black title bar, with the count in a white box on it like a stop's letter."""
    d.rectangle((0, 0, width - 1, height - 1), fill="white", outline="black", width=2)
    d.rectangle((0, 0, width - 1, BOXED_BAR), fill="black")
    d.text((10, BOXED_BAR // 2), _("Notices"), "white", fitted_font(SANS_SEMI, 14), anchor="lm")
    number, font = str(count), fitted_font(BOLD, 14)
    box_width = max(21, round(font.getlength(number)) + 10)
    d.rectangle((width - 9 - box_width, 3, width - 9, BOXED_BAR - 3), fill="white")
    d.text((width - 9 - box_width / 2, BOXED_BAR // 2), number, "black", font, anchor="mm")


@dataclass(frozen=True)
class Style:
    """A style's chrome, and the room it leaves the rows: `top` above them, `bottom` below, `inset` either side."""

    draw: Callable[[ImageDraw.ImageDraw, int, int, int], None]
    top: int
    bottom: int
    inset: int


STYLES: dict[NoticeStyle, Style] = {
    "airy": Style(_airy, top=24, bottom=2, inset=0),
    "hero": Style(_hero, top=32, bottom=2, inset=0),
    "card": Style(_card, top=CARD_BAR + 4, bottom=6, inset=8),
    "boxed": Style(_boxed, top=BOXED_BAR + 4, bottom=6, inset=8),
}
