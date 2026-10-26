"""The newspaper's "In brief" column: the notices, then what's playing and the developer's summary, a few lines each.

Each notice is its source in capitals over its text (two lines at most), with a rule under it, the most important
first; those that don't fit are counted. Under them, "On the turntable" (Spotify's song, or its top tracks) and "At the
workshop" (the developer panel's pull requests and builds), when the layout shows them.
"""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.developer_styles import _builds
from countdown_core.i18n import _
from countdown_core.newspaper.typesetting import CONTENT_TOP, headline, serif, serif_bold, wrapped
from countdown_core.notices.notice import Severity
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_styles import TOP
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.utils.clock import format_clock
from countdown_core.utils.scaled_draw import scaled_canvas
from countdown_core.utils.utils import truncate_to_fit

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.notices.notice import Notice
    from countdown_core.utils.clock import ClockFormat
    from countdown_core.utils.scaled_draw import ScaledDraw

# A notice: its source's size and baseline from the item's top, its text's size, how far apart its lines are and how
# many it may take, and the room under it (the rule's in the middle of it).
KICKER = 12
KICKER_BASELINE = 12
NOTICE_SIZE = 13
NOTICE_STEP = 16
NOTICE_LINES = 2
# With room for every notice at this many lines, each may take them.
ROOMY_LINES = 3
ITEM_GAP = 10
# A section under the notices: the room above it, its heading's baseline, its lines' size and how far apart they are.
SECTION_GAP = 8
SECTION_SIZE = 12
SECTION_STEP = 15
# How tall the line counting the notices that didn't fit is.
MORE_HEIGHT = 18
# A section: its heading, and its lines.
Section = tuple[str, list[str]]


class NewspaperBriefPanel(Panel):
    """The notices, then what's playing and the developer's summary, under the headline "In brief"."""

    scales = True

    def __init__(self, notices: Panel | None, music: Panel | None = None, developer: Panel | None = None) -> None:
        """`notices`: the notice board's panel (or a message); `music`: Spotify's; `developer`: the developer panel's.

        Each may be None, or a panel this column doesn't draw (Spotify's message, say), which leaves it out.
        """
        super().__init__()
        self.notices = notices
        self.music = music
        self.developer = developer

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        img, d = scaled_canvas(image_width, image_height)
        headline(d, image_width, _("In brief"))
        music, developer = music_section(self.music), developer_section(self.developer, image_width)
        sections = [section for section in (music, developer) if section is not None]
        sections_height = sum(_section_height(lines) for _heading, lines in sections)
        items = brief_items(self.notices, clock_format)
        y = _draw_items(d, items, (image_width, image_height - sections_height))
        if not items and not sections:
            d.text((0, CONTENT_TOP + 18), _("Nothing to report"), "black", serif(NOTICE_SIZE), anchor="ls")
            return img
        for heading, lines in sections:
            if y + _section_height(lines) > image_height:
                break
            y += SECTION_GAP
            kicker = serif_bold(KICKER)
            d.text((0, y + KICKER_BASELINE), truncate_to_fit(heading, kicker, image_width), "black", kicker, "ls")
            for i, line in enumerate(lines):
                text = truncate_to_fit(line, serif(SECTION_SIZE), image_width)
                d.text((0, y + KICKER_BASELINE + (i + 1) * SECTION_STEP), text, "black", serif(SECTION_SIZE), "ls")
            y += _section_height(lines) - SECTION_GAP
        return img


def _section_height(lines: list[str]) -> int:
    return SECTION_GAP + KICKER_BASELINE + len(lines) * SECTION_STEP + SECTION_GAP


def brief_items(notices: Panel | None, clock_format: ClockFormat) -> list[tuple[str, str, bool]]:
    """Return the notices as items, (source, text, planned) each, the most important first; a message as one."""
    if isinstance(notices, MessagePanel):
        return [(_(notices.title), _(notices.message), False)]
    if not isinstance(notices, NoticeBoardPanel):
        return []
    current = [notice for notice in notices.notices if notice.severity != Severity.PLANNED]
    planned = [notice for notice in notices.notices if notice.severity == Severity.PLANNED]
    return [
        (notice.source, _notice_text(notice, clock_format), notice.severity == Severity.PLANNED)
        for notice in current + planned
    ]


def _notice_text(notice: Notice, clock_format: ClockFormat) -> str:
    if notice.display_time is None:
        return notice.text
    return f"{notice.text} {format_clock(notice.display_time, clock_format)}"


def _draw_items(d: ScaledDraw, items: list[tuple[str, str, bool]], size: tuple[int, int]) -> float:
    """Draw as many notices as fit in `size` (planned works only if every other one did); return where they end.

    If any but planned works are left out, a last line says how many. With room for them all at ROOMY_LINES, each
    notice may take that many lines.
    """
    width, bottom = size
    font = serif(NOTICE_SIZE)
    y: float = CONTENT_TOP
    current_left = sum(1 for *_rest, planned in items if not planned)
    roomy = [wrapped(text, font, width, ROOMY_LINES) for _source, text, _planned in items]
    fits = CONTENT_TOP + sum(_item_height(lines) for lines in roomy) <= bottom
    for (source, text, planned), roomy_lines in zip(items, roomy, strict=True):
        lines = roomy_lines if fits else wrapped(text, font, width, NOTICE_LINES)
        height = _item_height(lines)
        reserve = MORE_HEIGHT if current_left > 1 else 0
        if y + height > bottom - reserve:
            break
        kicker = serif_bold(KICKER)
        d.text((0, y + KICKER_BASELINE), truncate_to_fit(source.upper(), kicker, width), "black", kicker, "ls")
        for i, line in enumerate(lines):
            d.text((0, y + KICKER_BASELINE + 2 + (i + 1) * NOTICE_STEP), line, "black", font, anchor="ls")
        y += height
        d.rectangle((0, y - ITEM_GAP / 2, width - 1, y - ITEM_GAP / 2), fill="black")
        current_left -= 0 if planned else 1
    if current_left:
        d.text((0, y + 13), _("+{count} more").format(count=current_left), "black", serif(SECTION_SIZE), anchor="ls")
        y += MORE_HEIGHT
    return y


def _item_height(lines: list[str]) -> int:
    return KICKER_BASELINE + 2 + len(lines) * NOTICE_STEP + ITEM_GAP


def music_section(music: Panel | None) -> Section | None:
    """Return what's playing ("On the turntable"): the song, its artist and what's next; or the top tracks.

    None for anything else (a message: nothing playing, or Spotify not set up).
    """
    heading = _("On the turntable")
    if isinstance(music, SpotifyPanel) and music.queue.currently_playing is not None:
        track = music.queue.currently_playing
        lines = [f"\N{LEFT DOUBLE QUOTATION MARK}{track.name}\N{RIGHT DOUBLE QUOTATION MARK}"]
        lines.append(", ".join(artist.name for artist in track.artists))
        if music.queue.queue:
            lines.append(f"{_(music.labels.next)}: {music.queue.queue[0].name}")
        return heading, lines
    if isinstance(music, SpotifyTopPanel) and music.tracks:
        lines = [f"{_(TOP['tracks'])}, {_(music.period)}"]
        lines += [f"{i}. {track.name}" for i, track in enumerate(music.tracks[:2], start=1)]
        return heading, lines
    return None


def developer_section(developer: Panel | None, width: int) -> Section | None:
    """Return the developer's summary ("At the workshop"): the pull requests and the builds; None without one.

    `width`: the column's, which a longer line wraps to.
    """
    if not isinstance(developer, DeveloperPanel):
        return None
    heading = _("At the workshop")
    github = developer.summary.github
    if github is None:
        return heading, wrapped(_("Add a GitHub token on the config page"), serif(SECTION_SIZE), width, 2)
    prs = f"{github.open_prs} {_('open PRs')}, {github.review_requests} {_('to review')}"
    return heading, [prs, _builds(github)]
