"""The looks a stop's departures can have (the config's style).

Each draws the whole stop, header included, into `width` x `height`, on a transparent background.
"""

from typing import TYPE_CHECKING

from PIL import Image, ImageFont

from countdown_core.i18n import _
from countdown_core.utils.scaled_draw import ScaledDraw, scaled_canvas
from countdown_core.utils.utils import ROUNDEL, fitted_font, google_symbols, resized, truncate_to_fit

if TYPE_CHECKING:
    from collections.abc import Callable

    from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel

BOLD = "Ubuntu-Bold.ttf"
MEDIUM = "Ubuntu-Medium.ttf"
REGULAR = "Ubuntu-Regular.ttf"
SANS = "GoogleSans-Regular.ttf"
SANS_SEMI = "GoogleSans-SemiBold.ttf"
# The smallest a badge's or name's text is shrunk to, to fit.
MIN_FONT_SIZE = 8

# Material Symbols glyphs, by AbstractArrivalPanel.icon
ICONS = {
    "bus": chr(0xE530),  # directions_bus
    "subway": chr(0xE56F),  # subway
    "tram": chr(0xE571),  # tram
    "train": chr(0xE570),  # train
    "ferry": chr(0xE532),  # directions_boat
}


def _canvas(width: int, height: int) -> tuple[Image.Image, ScaledDraw]:
    """Make a transparent canvas `width` x `height` in the panel's own pixels, at the drawing scale."""
    return scaled_canvas(width, height)


def _shrunk(text: str, filename: str, size: int, width: float) -> ImageFont.FreeTypeFont:
    """Load the font at `size`, or smaller until `text` fits `width`."""
    font = fitted_font(filename, size)
    while font.getlength(text) > width and size > MIN_FONT_SIZE:
        size -= 1
        font = fitted_font(filename, size)
    return font


# The most mode icons a header lists; a stop serving more keeps the first few, so its name keeps its room.
MAX_HEADER_ICONS = 3
# The room a row's own mode icon takes before the row, on a stop that serves several modes.
ROW_ICON_ROOM = 22


def _header_icons(d: ScaledDraw, panel: AbstractArrivalPanel, y: float, size: int, step: int) -> int:
    """Draw the modes the stop serves, side by side from the left and centred on `y`; return the width they took."""
    icons = panel.icons[:MAX_HEADER_ICONS]
    for i, icon in enumerate(icons):
        d.text((i * step, y), ICONS[icon], "black", resized(google_symbols(), size), anchor="lm")
    return len(icons) * step


def _row_icons(d: ScaledDraw, panel: AbstractArrivalPanel, centres: list[float]) -> None:
    """Draw each row's mode before it, when the stop serves several; `centres` are the rows' middles, top down."""
    if not panel.mixed_modes:
        return
    for departure, y in zip(panel.departures, centres, strict=False):
        if departure.icon:
            d.text((0, y), ICONS[departure.icon], "black", resized(google_symbols(), 18), anchor="lm")


def _minutes(minutes: int) -> str:
    return _("due") if minutes == 0 else str(minutes)


def _unit(minutes: int, unit: str) -> str:
    """Return the unit after a time, e.g. "min"; none after "due"."""
    return "" if minutes == 0 else unit


def _later(minutes: tuple[int, ...]) -> str:
    """Show the next two after the first, e.g. "then 9, 14 min"; empty if there are none."""
    return _("then {times} min").format(times=", ".join(map(str, minutes[1:3]))) if len(minutes) > 1 else ""


def hero(panel: AbstractArrivalPanel, width: int, height: int) -> Image.Image:
    """Draw the next departure big, so it reads from across the room; the rest in a list below."""
    img, d = _canvas(width, height)
    # Header: the stop's modes (a bus, a train...), its name, its letter in a circle, and a rule.
    name_x = _header_icons(d, panel, 11, 22, 24) + 2
    letter_width = 26 if panel.stop_letter else 0
    name = truncate_to_fit(panel.stop_name, fitted_font(SANS_SEMI, 17), width - name_x - letter_width)
    d.text((name_x, 11), name, "black", fitted_font(SANS_SEMI, 17), anchor="lm")
    if panel.stop_letter:
        d.ellipse((width - 22, 0, width - 1, 21), fill="black")
        d.text((width - 11.5, 11), panel.stop_letter, "white", fitted_font(BOLD, 13), anchor="mm")
    d.line((0, 26, width, 26), fill="black", width=2)
    if not panel.departures:
        return img

    # A stop serving several modes gives each row its own icon, and the rows a little less room.
    indent = ROW_ICON_ROOM if panel.mixed_modes else 0
    rows, centres = _hero_rows(panel, width - indent, height)
    d.paste(rows, (indent, 0))
    _row_icons(d, panel, centres)
    return img


def _hero_rows(panel: AbstractArrivalPanel, width: int, height: int) -> tuple[Image.Image, list[float]]:
    """Draw the departures under the header: the next big, the rest in a list; return the rows' middles too."""
    img, d = _canvas(width, height)
    first, *rest = panel.departures
    y = 34
    centres = [y + 20.0]
    badge_width = 46
    d.rounded_rectangle((0, y, badge_width, y + 40), 6, fill="black")
    d.text(
        (badge_width / 2, y + 20), first.badge, "white", _shrunk(first.badge, BOLD, 22, badge_width - 6), anchor="mm"
    )
    unit, unit_font, number_font = _unit(first.minutes[0], _("min")), fitted_font(MEDIUM, 13), fitted_font(BOLD, 40)
    number = _minutes(first.minutes[0])
    number_right = width - (unit_font.getlength(unit) + 4 if unit else 0)
    d.text((number_right, y + 38), number, "black", number_font, anchor="rs")
    d.text((width, y + 38), unit, "black", unit_font, anchor="rs")
    room = number_right - number_font.getlength(number) - badge_width - 16
    d.text(
        (badge_width + 8, y + 12),
        truncate_to_fit(first.destination, fitted_font(BOLD, 17), room),
        "black",
        fitted_font(BOLD, 17),
        anchor="lm",
    )
    d.text((badge_width + 8, y + 31), _later(first.minutes), "black", fitted_font(REGULAR, 12), anchor="lm")

    y += 50
    time_font, destination_font = fitted_font(BOLD, 14), fitted_font(MEDIUM, 14)
    for departure in rest:
        if y + 21 > height:  # the badge's box takes the pixel row at y + 20 too
            break
        d.rounded_rectangle((0, y, 38, y + 20), 4, outline="black", width=2)
        d.text((19, y + 10), departure.badge, "black", _shrunk(departure.badge, BOLD, 13, 32), anchor="mm")
        eta = " ".join(filter(None, (_minutes(departure.minutes[0]), _unit(departure.minutes[0], _("min")))))
        d.text((width, y + 10), eta, "black", time_font, anchor="rm")
        room = width - time_font.getlength(eta) - 54
        d.text(
            (46, y + 10),
            truncate_to_fit(departure.destination, destination_font, room),
            "black",
            destination_font,
            anchor="lm",
        )
        centres.append(y + 10)
        y += 25
    return img, centres


def airy(panel: AbstractArrivalPanel, width: int, height: int) -> Image.Image:
    """Draw a row per route, with its next departure big on the right and the ones after under it."""
    img, d = _canvas(width, height)
    # A bus or tram stop's letter, or a metro or railway platform's number.
    label = _("Platform {letter}") if panel.icon in ("subway", "train") else _("Stop {letter}")
    letter = label.format(letter=panel.stop_letter) if panel.stop_letter else ""
    letter_font = fitted_font(MEDIUM, 13)
    # The stop's modes (a bus, a train...), smaller than hero's.
    name_x = _header_icons(d, panel, 10, 18, 20) + 1
    name_room = width - name_x - (letter_font.getlength(letter) + 8 if letter else 0)
    d.text(
        (name_x, 0), truncate_to_fit(panel.stop_name, fitted_font(BOLD, 16), name_room), "black", fitted_font(BOLD, 16)
    )
    if letter:
        d.text((width, 0), letter, "black", letter_font, anchor="ra")
    d.line((0, 24, width, 24), fill="black", width=2)

    # A stop serving several modes gives each row its own icon, and the rows a little less room.
    indent = ROW_ICON_ROOM if panel.mixed_modes else 0
    rows, centres = _airy_rows(panel, width - indent, height)
    d.paste(rows, (indent, 0))
    _row_icons(d, panel, centres)
    return img


def _airy_rows(panel: AbstractArrivalPanel, width: int, height: int) -> tuple[Image.Image, list[float]]:
    """Draw a row per route under the header; return the rows' middles too."""
    img, d = _canvas(width, height)
    centres: list[float] = []
    number_font, unit_font, small = fitted_font(BOLD, 26), fitted_font(REGULAR, 11), fitted_font(REGULAR, 11)
    y = 32
    for departure in panel.departures:
        if y + 31 > height:
            break
        centres.append(y + 15)
        d.rectangle((0, y, 3, y + 30), fill="black")
        number, unit = _minutes(departure.minutes[0]), _unit(departure.minutes[0], _("min"))
        unit_width = unit_font.getlength(unit) + 3 if unit else 0
        number_left = width - unit_width - number_font.getlength(number)
        d.text((width, y + 28), unit, "black", unit_font, anchor="rs")
        d.text((width - unit_width, y + 28), number, "black", number_font, anchor="rs")
        if panel.is_tube:
            # Every train on a line has the same badge: the destination leads instead.
            title = truncate_to_fit(departure.destination, fitted_font(BOLD, 15), number_left - 18)
            d.text((10, y), title, "black", fitted_font(BOLD, 15))
            subtitle = _later(departure.minutes) or _("{line} line").format(line=departure.line)
        else:
            route_font = fitted_font(BOLD, 18)
            d.text((10, y), departure.badge, "black", route_font)
            destination_x = 10 + route_font.getlength(departure.badge) + 8
            destination = truncate_to_fit(
                departure.destination, fitted_font(MEDIUM, 13), number_left - destination_x - 8
            )
            d.text((destination_x, y + 4), destination, "black", fitted_font(MEDIUM, 13))
            subtitle = _later(departure.minutes)
        d.text((10, y + 21), subtitle, "black", small)
        y += 37
        d.line((10, y - 4, width, y - 4), fill="gray")
    return img, centres


def card(panel: AbstractArrivalPanel, width: int, height: int) -> Image.Image:
    """Draw the stop in a rounded card.

    A black title bar, then a row per route with its minutes lined up in a column on the right.
    """
    img, d = _canvas(width, height)
    d.rounded_rectangle((0, 0, width - 1, height - 1), 10, fill="white", outline="black", width=2)
    d.rounded_rectangle((0, 0, width - 1, 30), 10, fill="black")
    d.rectangle((0, 20, width - 1, 30), fill="black")  # square off the bar's bottom corners
    x = 10
    if panel.is_tube:
        roundel = ROUNDEL.resize((ROUNDEL.width * d.scale, ROUNDEL.height * d.scale), Image.Resampling.LANCZOS)
        d.paste(roundel, (x, 7))
        x += ROUNDEL.size[0] + 6
    letter_room = 34 if panel.stop_letter else 10
    title_font = fitted_font(SANS_SEMI, 16)
    d.text(
        (x, 15), truncate_to_fit(panel.stop_name, title_font, width - x - letter_room), "white", title_font, anchor="lm"
    )
    if panel.stop_letter:
        d.rounded_rectangle((width - 30, 5, width - 9, 25), 4, fill="white")
        d.text((width - 19.5, 15), panel.stop_letter, "black", fitted_font(BOLD, 14), anchor="mm")

    number_font, unit_font, destination_font = fitted_font(BOLD, 20), fitted_font(REGULAR, 12), fitted_font(SANS, 15)
    y = 38
    for i, departure in enumerate(panel.departures):
        if y + 24 > height - 6:
            break
        if i:
            for dot in range(10, width - 10, 4):
                d.point((dot, y - 3), fill="black")
        d.text((10, y + 12), departure.badge, "black", _shrunk(departure.badge, BOLD, 16, 40), anchor="lm")
        number = _minutes(departure.minutes[0])
        # "due" has no unit after it, so it takes the unit's column too.
        number_right = width - 32 if departure.minutes[0] else width - 10
        d.text((number_right, y + 12), number, "black", number_font, anchor="rm")
        if departure.minutes[0]:
            # Translators: short for minutes, after a number in a narrow column ("4 m").
            d.text((width - 10, y + 15), _("m"), "black", unit_font, anchor="rm")
        room = number_right - number_font.getlength(number) - 62
        d.text(
            (56, y + 12),
            truncate_to_fit(departure.destination, destination_font, room),
            "black",
            destination_font,
            anchor="lm",
        )
        y += 27
    return img


STYLES: dict[str, Callable[[AbstractArrivalPanel, int, int], Image.Image]] = {
    "hero": hero,
    "airy": airy,
    "card": card,
}
