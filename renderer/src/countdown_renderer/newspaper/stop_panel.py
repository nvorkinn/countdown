"""A stop's column on the newspaper's front page: its name as the headline, the next departure big, then the rest.

Under the headline, a deck says what leaves from it ("Buses, departing", "Northern line, departing"). The next
departure is a big number of minutes with its route, destination and later times beside it; every other route follows,
a row each, with its destination, a dotted leader and its next time against the right edge.
"""

from typing import TYPE_CHECKING, override

from countdown_core.i18n import N_, _, ngettext
from countdown_renderer.newspaper.typesetting import CONTENT_TOP, headline, leaders, serif, serif_bold, shrunk, wrapped
from countdown_renderer.panel import Panel
from countdown_renderer.system_screens.message_panel import MessagePanel
from countdown_renderer.tfl.abstract_arrival_panel import AbstractArrivalPanel
from countdown_renderer.utils.scaled_draw import scaled_canvas
from countdown_renderer.utils.utils import truncate_to_fit

if TYPE_CHECKING:
    from PIL import Image

    from countdown_renderer.tfl.abstract_arrival_panel import Departures, Icon
    from countdown_renderer.utils.clock import ClockFormat
    from countdown_renderer.utils.scaled_draw import ScaledDraw

# The next departure: the big number's size and baseline, below the column's headline; where the rule under it is.
NEXT_SIZE = 75
NEXT_BASELINE = CONTENT_TOP + 68
NEXT_RULE = CONTENT_TOP + 100
# The other routes: where the first row's baseline is, how far apart the rows are, and their type's size.
ROWS_TOP = NEXT_RULE + 26
ROW_STEP = 27
ROW_SIZE = 16
# The least room the badges' column takes, and between it and the destinations.
BADGE_ROOM = 40
BADGE_GAP = 12
# What leaves from a stop that isn't a tube station, by its mode: English, translated where drawn.
MODES: dict[Icon, str] = {
    "bus": N_("Buses"),
    "subway": N_("Metro"),
    "tram": N_("Trams"),
    "train": N_("Trains"),
    "ferry": N_("Ferries"),
}


class NewspaperStopPanel(Panel):
    """One stop's column: the headline and deck, the next departure big, and every other route a row each.

    A message in the departures' place (no stops set up) has its title as the headline and says why under it.
    """

    scales = True

    def __init__(self, stop: Panel) -> None:
        """`stop`: one stop's departures (as layouts.hd.stops_of gives them), or a message in their place."""
        super().__init__()
        self.stop = stop

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        stop = self.stop
        if isinstance(stop, MessagePanel):
            headline(d, image_width, _(stop.title))
            _paragraph(d, _(stop.message), image_width)
            return img
        if not isinstance(stop, AbstractArrivalPanel):
            return img
        headline(d, image_width, stop.stop_name, deck(stop))
        if not stop.departures:
            _paragraph(d, _("No departures"), image_width)
            return img
        first, *rest = stop.departures
        _draw_next(d, first, image_width)
        d.rectangle((0, NEXT_RULE, image_width - 1, NEXT_RULE), fill="black")
        _draw_rows(d, rest, (image_width, image_height))
        return img


def deck(stop: AbstractArrivalPanel) -> str:
    """Return what the headline's deck says leaves from `stop`: its tube lines or its modes ("Buses, departing")."""
    if stop.is_tube:
        lines = list(dict.fromkeys(departure.line for departure in stop.departures))
        if not lines:
            return ""
        what = ngettext("{lines} line", "{lines} lines", len(lines)).format(lines=" & ".join(lines))
    else:
        what = " & ".join(_(MODES[icon]) for icon in stop.icons)
    return _("{what}, departing").format(what=what)


def _paragraph(d: ScaledDraw, text: str, width: int) -> None:
    """Draw a few lines of text under the headline: why there's nothing to show."""
    font = serif(ROW_SIZE)
    for i, line in enumerate(wrapped(text, font, width, 3)):
        d.text((0, CONTENT_TOP + 20 + i * 22), line, "black", font, anchor="ls")


def _draw_next(d: ScaledDraw, first: Departures, width: int) -> None:
    """Draw the next departure: its minutes big, "min" beside them, and its route, destination and later times."""
    minutes = first.minutes[0]
    number = _("due") if minutes == 0 else str(minutes)
    number_font = shrunk(number, serif_bold(NEXT_SIZE), width * 0.45)
    d.text((0, NEXT_BASELINE), number, "black", number_font, anchor="ls")
    x = number_font.getlength(number) + 4
    if minutes:
        unit_font = serif(20)
        d.text((x, NEXT_BASELINE + 3), _("min"), "black", unit_font, anchor="ls")
        x += unit_font.getlength(_("min"))
    x += 10
    room = width - x
    badge_font = shrunk(first.badge, serif_bold(22), room)
    d.text((x, NEXT_BASELINE - 38), first.badge, "black", badge_font, anchor="ls")
    destination_font = serif(18)
    d.text(
        (x, NEXT_BASELINE - 14),
        truncate_to_fit(first.destination, destination_font, room),
        "black",
        destination_font,
        anchor="ls",
    )
    if len(first.minutes) > 1:
        later = _("then {times} min").format(times=", ".join(map(str, first.minutes[1:3])))
        d.text((x, NEXT_BASELINE + 3), truncate_to_fit(later, serif(12), room), "black", serif(12), anchor="ls")


def _draw_rows(d: ScaledDraw, rows: list[Departures], size: tuple[int, int]) -> None:
    """Draw a row for each of the other routes, as many as fit: badge, destination, leader, and the next time."""
    width, height = size
    badge_font, destination_font = serif_bold(ROW_SIZE), serif(ROW_SIZE)
    badge_room = max([BADGE_ROOM, *(badge_font.getlength(row.badge) for row in rows)]) + BADGE_GAP
    time_room = badge_font.getlength("99") + 20
    baseline = ROWS_TOP
    for row in rows:
        if baseline + 6 > height:
            return
        minutes = row.minutes[0]
        time = _("due") if minutes == 0 else str(minutes)
        d.text((0, baseline), truncate_to_fit(row.badge, badge_font, badge_room - 4), "black", badge_font, anchor="ls")
        destination = truncate_to_fit(row.destination, destination_font, width - badge_room - time_room)
        d.text((badge_room, baseline), destination, "black", destination_font, anchor="ls")
        d.text((width, baseline), time, "black", badge_font, anchor="rs")
        end = badge_room + destination_font.getlength(destination)
        leaders(d, (end, width - badge_font.getlength(time)), baseline)
        baseline += ROW_STEP
