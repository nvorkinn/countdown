"""The looks a stop's departures can have besides classic (the config's tfl.style). Each draws
the whole stop, header included, into `width` x `height`, on a transparent background."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from countdown_core.utils.utils import ROUNDEL, fitted_font, google_symbols, truncate_to_fit

if TYPE_CHECKING:
    from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel

BOLD = "Ubuntu-Bold.ttf"
MEDIUM = "Ubuntu-Medium.ttf"
REGULAR = "Ubuntu-Regular.ttf"
SANS = "GoogleSans-Regular.ttf"
SANS_SEMI = "GoogleSans-SemiBold.ttf"

# Material Symbols glyphs, by AbstractArrivalPanel.icon
ICONS = {
    "bus": chr(0xE530),  # directions_bus
    "subway": chr(0xE56F),  # subway
    "tram": chr(0xE571),  # tram
    "train": chr(0xE570),  # train
    "ferry": chr(0xE532),  # directions_boat
}


def _canvas(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    return img, draw


def _shrunk(text: str, filename: str, size: int, width: float) -> ImageFont.FreeTypeFont:
    """The font at `size`, or smaller until `text` fits `width`."""
    font = fitted_font(filename, size)
    while font.getlength(text) > width and size > 8:
        size -= 1
        font = fitted_font(filename, size)
    return font


def _minutes(minutes: int) -> str:
    return "due" if minutes == 0 else str(minutes)


def _later(minutes: tuple[int, ...]) -> str:
    """The next two after the first, e.g. "then 9, 14 min"; empty if there are none."""
    return f"then {', '.join(map(str, minutes[1:3]))} min" if len(minutes) > 1 else ""


def hero(panel: AbstractArrivalPanel, width: int, height: int) -> Image.Image:
    """The next departure big, so it reads from across the room; the rest in a list below."""
    img, d = _canvas(width, height)
    # Header: the stop's mode (a bus, a train...), its name, its letter in a circle, and a rule.
    d.text((0, 11), ICONS[panel.icon], "black", google_symbols().font_variant(size=22), anchor="lm")
    letter_width = 26 if panel.stop_letter else 0
    name = truncate_to_fit(panel.stop_name, fitted_font(SANS_SEMI, 17), width - 26 - letter_width)
    d.text((26, 11), name, "black", fitted_font(SANS_SEMI, 17), anchor="lm")
    if panel.stop_letter:
        d.ellipse((width - 22, 0, width - 1, 21), fill="black")
        d.text((width - 11.5, 11), panel.stop_letter, "white", fitted_font(BOLD, 13), anchor="mm")
    d.line((0, 26, width, 26), fill="black", width=2)
    if not panel.departures:
        return img

    first, *rest = panel.departures
    y = 34
    badge_width = 46
    d.rounded_rectangle((0, y, badge_width, y + 40), 6, fill="black")
    d.text(
        (badge_width / 2, y + 20), first.badge, "white", _shrunk(first.badge, BOLD, 22, badge_width - 6), anchor="mm"
    )
    unit, unit_font, number_font = "min", fitted_font(MEDIUM, 13), fitted_font(BOLD, 40)
    number = _minutes(first.minutes[0])
    number_right = width - unit_font.getlength(unit) - 4
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
        if y + 20 > height:
            break
        d.rounded_rectangle((0, y, 38, y + 20), 4, outline="black", width=2)
        d.text((19, y + 10), departure.badge, "black", _shrunk(departure.badge, BOLD, 13, 32), anchor="mm")
        eta = f"{_minutes(departure.minutes[0])} min"
        d.text((width, y + 10), eta, "black", time_font, anchor="rm")
        room = width - time_font.getlength(eta) - 54
        d.text(
            (46, y + 10),
            truncate_to_fit(departure.destination, destination_font, room),
            "black",
            destination_font,
            anchor="lm",
        )
        y += 25
    return img


def grouped(panel: AbstractArrivalPanel, width: int, height: int) -> Image.Image:
    """A row per route, with its next departure big on the right and the ones after under it."""
    img, d = _canvas(width, height)
    letter = f"Stop {panel.stop_letter}" if panel.stop_letter else ""
    letter_font = fitted_font(MEDIUM, 13)
    name_room = width - (letter_font.getlength(letter) + 8 if letter else 0)
    d.text((0, 0), truncate_to_fit(panel.stop_name, fitted_font(BOLD, 16), name_room), "black", fitted_font(BOLD, 16))
    if letter:
        d.text((width, 0), letter, "black", letter_font, anchor="ra")
    d.line((0, 24, width, 24), fill="black", width=2)

    number_font, unit_font, small = fitted_font(BOLD, 26), fitted_font(REGULAR, 11), fitted_font(REGULAR, 11)
    unit_width = unit_font.getlength("min") + 3
    y = 32
    for departure in panel.departures:
        if y + 31 > height:
            break
        d.rectangle((0, y, 3, y + 30), fill="black")
        number = _minutes(departure.minutes[0])
        number_left = width - unit_width - number_font.getlength(number)
        d.text((width, y + 28), "min", "black", unit_font, anchor="rs")
        d.text((width - unit_width, y + 28), number, "black", number_font, anchor="rs")
        if panel.is_tube:
            # Every train on a line has the same badge: the destination leads instead.
            title = truncate_to_fit(departure.destination, fitted_font(BOLD, 15), number_left - 18)
            d.text((10, y), title, "black", fitted_font(BOLD, 15))
            subtitle = _later(departure.minutes) or f"{departure.line} line"
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
    return img


def card(panel: AbstractArrivalPanel, width: int, height: int) -> Image.Image:
    """The stop in a rounded card: a black title bar, then a row per route with its minutes
    lined up in a column on the right."""
    img, d = _canvas(width, height)
    d.rounded_rectangle((0, 0, width - 1, height - 1), 10, fill="white", outline="black", width=2)
    d.rounded_rectangle((0, 0, width - 1, 30), 10, fill="black")
    d.rectangle((0, 20, width - 1, 30), fill="black")  # square off the bar's bottom corners
    x = 10
    if panel.is_tube:
        img.paste(ROUNDEL, (x, 7), ROUNDEL)
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
        d.text((width - 32, y + 12), number, "black", number_font, anchor="rm")
        d.text((width - 10, y + 15), "m", "black", unit_font, anchor="rm")
        room = width - 32 - number_font.getlength(number) - 62
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
    "grouped": grouped,
    "card": card,
}
