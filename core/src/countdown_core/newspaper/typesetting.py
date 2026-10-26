"""The newspaper's type and rules: DejaVu Serif (fonts/DejaVuSerif-LICENSE.txt), set in a panel's own pixels.

Every helper takes a ScaledDraw, so the panels are laid out at the 7.5" screens' sizes and drawn bigger.
"""

from typing import TYPE_CHECKING

from countdown_core.utils.utils import fitted_font, resized, truncate_to_fit

if TYPE_CHECKING:
    from PIL import ImageFont

    from countdown_core.utils.scaled_draw import ScaledDraw

SERIF = "DejaVuSerif.ttf"
SERIF_BOLD = "DejaVuSerif-Bold.ttf"
# A column's headline: its size, where the deck under it sits (baseline), and the rule under both; where the column's
# own content starts.
HEADLINE = 24
DECK = 13
DECK_BASELINE = 44
HEADLINE_RULE = 52
CONTENT_TOP = 60
# The dots between a row's label and its value: how far apart, and the least room they leave either side.
LEADER_STEP = 4
LEADER_GAP = 5


def serif(size: int) -> ImageFont.FreeTypeFont:
    """Return DejaVu Serif at `size`, in a panel's own pixels."""
    return fitted_font(SERIF, size)


def serif_bold(size: int) -> ImageFont.FreeTypeFont:
    """Return DejaVu Serif Bold at `size`, in a panel's own pixels."""
    return fitted_font(SERIF_BOLD, size)


def shrunk(text: str, font: ImageFont.FreeTypeFont, width: float, smallest: int = 8) -> ImageFont.FreeTypeFont:
    """Return `font`, or a smaller size of it (down to `smallest`) that sets `text` within `width`."""
    while font.getlength(text) > width and font.size > smallest:
        font = resized(font, int(font.size) - 1)
    return font


def wrapped(text: str, font: ImageFont.FreeTypeFont, width: float, lines: int) -> list[str]:
    """Break `text` into at most `lines` lines no wider than `width`, the last cut short with an ellipsis if need be."""
    found: list[str] = []
    words = text.split()
    while words and len(found) < lines:
        line = words.pop(0)
        while words and font.getlength(f"{line} {words[0]}") <= width:
            line = f"{line} {words.pop(0)}"
        found.append(line)
    if words and found:
        found[-1] = truncate_to_fit(f"{found[-1]} {' '.join(words)}", font, width)
    return [truncate_to_fit(line, font, width) for line in found]


def headline(d: ScaledDraw, width: int, title: str, deck: str = "") -> None:
    """Draw a column's headline across its top, the deck (a line saying what's under it) below, and a rule."""
    d.text((0, 0), title, "black", shrunk(title, serif_bold(HEADLINE), width), anchor="la")
    if deck:
        d.text((0, DECK_BASELINE), truncate_to_fit(deck, serif(DECK), width), "black", serif(DECK), anchor="ls")
    d.rectangle((0, HEADLINE_RULE, width - 1, HEADLINE_RULE), fill="black")


def leaders(d: ScaledDraw, span: tuple[float, float], baseline: float) -> None:
    """Draw a dotted leader along `baseline` between the x coordinates `span` (a label's end and a value's start)."""
    start, end = span
    x = start + LEADER_GAP
    while x <= end - LEADER_GAP:
        d.point((round(x), round(baseline) - 1), fill="black")
        x += LEADER_STEP


def table_row(  # noqa: PLR0913, PLR0917 -- a drawing helper: where, what, how wide, and in which fonts
    d: ScaledDraw,
    baseline: float,
    span: tuple[float, float],
    label: str,
    value: str,
    fonts: tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont],
) -> None:
    """Draw `label` from the left of `span` and `value` against its right, a dotted leader between them."""
    left, right = span
    label_font, value_font = fonts
    value_width = value_font.getlength(value)
    label = truncate_to_fit(label, label_font, right - left - value_width - 2 * LEADER_GAP)
    d.text((left, baseline), label, "black", label_font, anchor="ls")
    d.text((right, baseline), value, "black", value_font, anchor="rs")
    leaders(d, (left + label_font.getlength(label), right - value_width), baseline)


def double_rule(d: ScaledDraw, y: float, span: tuple[float, float]) -> None:
    """Draw a newspaper's double rule, thick over thin, from `y` down, between the x coordinates `span`."""
    left, right = span
    d.rectangle((left, y, right - 1, y + 1), fill="black")
    d.rectangle((left, y + 4, right - 1, y + 4), fill="black")
