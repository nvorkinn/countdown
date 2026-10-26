"""The newspaper's masthead: its name (the first stop's, in capitals) over a double rule, and the dateline under it.

The dateline has the date on the left, the edition in the middle (morning, afternoon or late, by the time of day) and
the time on the right, with a thin rule under it.
"""

from typing import TYPE_CHECKING, override

from countdown_core.core import moment
from countdown_core.core.panel import Panel
from countdown_core.i18n import _, format_date, local_zone
from countdown_core.newspaper.typesetting import double_rule, serif, serif_bold, shrunk
from countdown_core.utils.clock import format_clock
from countdown_core.utils.scaled_draw import scaled_canvas

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.utils.clock import ClockFormat

# Its height, in its own pixels; the room either side of it and above the name.
HEIGHT = 120
MARGIN = 28
# The name's size; the double rule under it; the dateline's baseline and size; the thin rule under that.
NAME_SIZE = 42
NAME_RULE = 87
DATELINE_BASELINE = 109
DATELINE_SIZE = 13
DATELINE_RULE = 118
# The hours the morning and afternoon editions end at; the late edition runs to midnight.
AFTERNOON_FROM = 12
LATE_FROM = 17


class NewspaperMastheadPanel(Panel):
    """The newspaper's name across the top, then the date, the edition and the time."""

    scales = True

    def __init__(self, place: str | None) -> None:
        """`place`: what the newspaper is named for (the first stop), or None for its name without one."""
        super().__init__()
        self.place = place

    @property
    def name(self) -> str:
        """The newspaper's name, in capitals: "THE ELEPHANT & CASTLE"."""
        name = _("The {place}").format(place=self.place) if self.place else _("The Daily Countdown")
        return name.upper()

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        span = image_width - 2 * MARGIN
        name = self.name
        d.text((image_width / 2, MARGIN), name, "black", shrunk(name, serif_bold(NAME_SIZE), span), anchor="ma")
        double_rule(d, NAME_RULE, (MARGIN, image_width - MARGIN))
        d.rectangle((MARGIN, DATELINE_RULE, image_width - MARGIN - 1, DATELINE_RULE), fill="black")

        now = moment.now().astimezone(local_zone())
        font = serif(DATELINE_SIZE)
        date = format_date(now.date(), _("EEEE d MMMM")).upper()
        d.text((MARGIN, DATELINE_BASELINE), date, "black", font, anchor="ls")
        d.text((image_width / 2, DATELINE_BASELINE), edition(now.hour).upper(), "black", font, anchor="ms")
        clock = format_clock(now, clock_format)
        d.text((image_width - MARGIN, DATELINE_BASELINE), clock, "black", serif_bold(DATELINE_SIZE), anchor="rs")
        return img


def edition(hour: int) -> str:
    """Return the edition for the hour of the day: the morning's, the afternoon's, or the late edition."""
    if hour < AFTERNOON_FROM:
        return _("Morning edition")
    if hour < LATE_FROM:
        return _("Afternoon edition")
    return _("Late edition")
