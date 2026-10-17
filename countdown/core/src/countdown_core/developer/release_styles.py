"""A release's progress as GitHub's workflow graph, in each of the config's styles.

The jobs go left to right in columns, each after the jobs it waits for, with a line from each job to the ones that
wait for it. A chain of more than four jobs wraps onto a second row when it fits, so the names stay readable. Each
style draws into `width` x `height`, on a transparent background.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from countdown_core.i18n import _
from countdown_core.spotify.spotify_styles import CARD_BAR, _card, _hero_header, airy_label
from countdown_core.tfl.arrival_styles import BOLD, MEDIUM, SANS, SANS_SEMI, _canvas
from countdown_core.utils.utils import fitted_font, google_symbols, resized, truncate_to_fit

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from PIL import Image, ImageDraw, ImageFont

    from countdown_core.developer.models import JobState, Release, ReleaseJob

# Material Symbols glyphs
ROCKET = chr(0xEB9B)
ICONS: dict[JobState, str] = {
    "done": chr(0xE86C),  # check_circle
    "failed": chr(0xE5C9),  # cancel
    "running": chr(0xE9D0),  # progress_activity
    "approve": chr(0xE764),  # back_hand: waiting for the owner's approval
    "queued": chr(0xE836),  # radio_button_unchecked: not started yet
    "skipped": chr(0xF08F),  # do_not_disturb_on
}

NODE_HEIGHT = 22
NODE_GAP = 7  # between the jobs of a column
ROW_GAP = 20  # between the two rows of a wrapped chain
LINE_GAP = 14  # the least room between columns, for the lines
WIDEST = 150  # a job's box is no wider than this, however few columns
LONGEST_ROW = 4  # a chain longer than this wraps, if it fits


@dataclass(frozen=True)
class Look:
    """How a style draws the graph's boxes."""

    text: ImageFont.FreeTypeFont
    running: ImageFont.FreeTypeFont  # a running job's name stands out
    count: ImageFont.FreeTypeFont
    icon_size: int
    outline: int
    radius: int
    filled: frozenset[JobState]  # drawn white on black: what needs the owner's eye


def aside(release: Release, others: int, now: datetime) -> str:
    """Return the header's right-hand side: how long it's taken, what waits for the owner, other releases."""
    minutes = release.minutes(now)
    if release.state == "failed":
        text = _("failed after {n} min").format(n=minutes)
    elif release.state == "done":
        text = _("released in {n} min").format(n=minutes)
    elif release.waiting:
        text = _("{n} min · approve {job}").format(n=minutes, job=release.waiting[0])
    else:
        text = _("{n} min").format(n=minutes)
    return _("+{n} more").format(n=others) + f" · {text}" if others else text


def _columns(jobs: list[ReleaseJob]) -> list[list[ReleaseJob]]:
    """Return the jobs in columns: each one column after the furthest of the jobs it waits for."""
    by_key = {job.key: job for job in jobs}
    depth: dict[str, int] = {}

    def of(key: str, seen: frozenset[str] = frozenset()) -> int:
        if key not in depth:
            # A need that isn't a job (or a loop, which GitHub refuses anyway) counts for nothing.
            needs = [n for n in by_key[key].needs if n in by_key and n not in seen]
            depth[key] = 1 + max((of(n, seen | {key}) for n in needs), default=-1)
        return depth[key]

    for job in jobs:
        of(job.key)
    return [[job for job in jobs if depth[job.key] == c] for c in range(max(depth.values(), default=-1) + 1)]


def _lines(jobs: list[ReleaseJob]) -> dict[str, list[str]]:
    """Return each job's needs, less those another of its needs already waits for: those lines only add clutter."""
    by_key = {job.key: job for job in jobs}
    upstream: dict[str, set[str]] = {}

    def above(key: str) -> set[str]:
        if key not in upstream:
            upstream[key] = set()  # a loop stops here
            for n in by_key[key].needs:
                if n in by_key:
                    upstream[key] |= {n} | above(n)
        return upstream[key]

    return {
        job.key: [
            n for n in job.needs if n in by_key and not any(n in above(m) for m in job.needs if m != n and m in by_key)
        ]
        for job in jobs
    }


def _rows(columns: list[list[ReleaseJob]], room: float) -> list[list[list[ReleaseJob]]]:
    """Return the columns in one row, or a long chain's in two, if both rows fit in `room`."""
    if len(columns) <= LONGEST_ROW:
        return [columns]
    half = (len(columns) + 1) // 2
    two = [columns[:half], columns[half:]]
    return two if sum(_height(max(len(c) for c in row)) for row in two) + ROW_GAP <= room else [columns]


def _height(jobs: int) -> float:
    return jobs * (NODE_HEIGHT + NODE_GAP) - NODE_GAP


def graph(d: ImageDraw.ImageDraw, release: Release, box: tuple[float, float, float, float], look: Look) -> None:
    """Draw the release's jobs as a graph in `box` (left, top, right, bottom)."""
    if not release.jobs:  # a run that hasn't started its jobs yet: the header says enough
        return
    left, top, right, bottom = box
    rows = _rows(_columns(release.jobs), bottom - top)
    # More jobs in a column than fit: the last box that fits says how many more there are.
    fits = max(1, int((bottom - top + NODE_GAP) // (NODE_HEIGHT + NODE_GAP)) // len(rows))
    per_row = max(len(row) for row in rows)
    node_w = min(WIDEST, (right - left - LINE_GAP * (per_row - 1)) / per_row)
    gap = (right - left - node_w * per_row) / (per_row - 1) if per_row > 1 else 0
    heights = [_height(min(fits, max(len(c) for c in row))) for row in rows]
    y = top + (bottom - top - sum(heights) - ROW_GAP * (len(rows) - 1)) / 2
    where: dict[str, tuple[float, float, int]] = {}
    hidden: list[tuple[float, float, int]] = []
    for r, row in enumerate(rows):
        for c, column in enumerate(row):
            shown = column if len(column) <= fits else column[: fits - 1]
            y_job = y + (heights[r] - _height(min(fits, len(column)))) / 2
            for job in shown:
                where[job.key] = (left + c * (node_w + gap), y_job, r)
                y_job += NODE_HEIGHT + NODE_GAP
            if len(shown) < len(column):
                hidden.append((left + c * (node_w + gap), y_job, len(column) - len(shown)))
        y += heights[r] + ROW_GAP
    _draw_lines(d, release.jobs, where, node_w, gap)
    for job in release.jobs:
        if job.key in where:
            _box(d, job, where[job.key][:2], node_w, look)
    for x, y_more, more in hidden:
        d.rounded_rectangle((x, y_more, x + node_w, y_more + NODE_HEIGHT), look.radius, fill="white", outline="black")
        d.text((x + node_w / 2, y_more + NODE_HEIGHT / 2 + 1), f"+{more}", "black", look.text, anchor="mm")


def _draw_lines(
    d: ImageDraw.ImageDraw,
    jobs: list[ReleaseJob],
    where: dict[str, tuple[float, float, int]],
    node_w: float,
    gap: float,
) -> None:
    """Draw a line from each job to each that waits for it, under the boxes.

    Along a row: out of the job's right, along to halfway, then into the waiting job's left. Onto the next row: down
    out of the job, back along the gap between the rows, and down into the waiting job.
    """
    middle = NODE_HEIGHT / 2
    lines = _lines(jobs)
    for job in jobs:
        if job.key not in where:
            continue
        x, y, row = where[job.key]
        for need in lines[job.key]:
            if need not in where:
                continue
            nx, ny, need_row = where[need]
            if need_row == row:
                half = x - gap / 2
                points = [(nx + node_w, ny + middle), (half, ny + middle), (half, y + middle), (x, y + middle)]
            else:
                between = y - ROW_GAP / 2
                points = [
                    (nx + node_w / 2, ny + NODE_HEIGHT),
                    (nx + node_w / 2, between),
                    (x + node_w / 2, between),
                    (x + node_w / 2, y),
                ]
            d.line(points, fill="black", width=1)


def _box(d: ImageDraw.ImageDraw, job: ReleaseJob, at: tuple[float, float], width: float, look: Look) -> None:
    """Draw a job's box: its state's icon, its name, and how many of its jobs are done if it's several."""
    x, y = at
    filled = job.state in look.filled
    ink = "white" if filled else "black"
    rect = (x, y, x + width, y + NODE_HEIGHT)
    if filled:
        d.rounded_rectangle(rect, look.radius, fill="black")
    else:
        # Without filled boxes, the ones that need the owner's eye get a heavier outline instead.
        outline = look.outline + (1 if job.state in {"approve", "failed"} and not look.filled else 0)
        d.rounded_rectangle(rect, look.radius, fill="white", outline="black", width=outline)
    middle = y + NODE_HEIGHT / 2
    d.text((x + 5, middle), ICONS[job.state], ink, resized(google_symbols(), look.icon_size), anchor="lm")
    text_x = x + 7 + look.icon_size
    room = x + width - 6 - text_x
    if job.total is not None:
        count = f"{job.done or 0}/{job.total}"
        d.text((x + width - 6, middle + 1), count, ink, look.count, anchor="rm")
        room -= look.count.getlength(count) + 4
    font = look.running if job.state == "running" else look.text
    d.text((text_x, middle + 1), truncate_to_fit(job.name, font, room), ink, font, anchor="lm")


def _title(release: Release) -> str:
    return f"{release.repo} {release.title}"


# --- airy: a small-caps label over a hairline, and hairline boxes ----------------------------


def airy(release: Release, others: int, now: datetime, width: int, height: int) -> Image.Image:
    """Draw the graph in hairline boxes under a small-caps label naming the release, beside how it's going."""
    img, d = _canvas(width, height)
    label = fitted_font(SANS_SEMI, 11)
    title = truncate_to_fit(_title(release).upper(), label, width * 0.5)
    end = airy_label(d, 0, 9, ROCKET, title)
    d.text(
        (width, 9),
        truncate_to_fit(aside(release, others, now).upper(), label, width - end - 16),
        "black",
        label,
        anchor="rm",
    )
    d.line((0, 19, width, 19), fill="black")
    look = Look(fitted_font(SANS, 12), fitted_font(SANS_SEMI, 12), fitted_font(SANS, 11), 13, 1, 4, frozenset())
    graph(d, release, (0, 26, width - 1, height - 2), look)
    return img


# --- hero: an icon header over a heavy rule, and what needs the owner in black ---------------


def hero(release: Release, others: int, now: datetime, width: int, height: int) -> Image.Image:
    """Draw the graph under an icon header naming the release, a job waiting for approval or failed in black."""
    img, d = _canvas(width, height)
    side = aside(release, others, now)
    side = truncate_to_fit(side, fitted_font(MEDIUM, 13), width * 0.5)
    title = truncate_to_fit(
        _title(release), fitted_font(SANS_SEMI, 17), width - 26 - fitted_font(MEDIUM, 13).getlength(side) - 16
    )
    _hero_header(d, 0, width, ROCKET, title, side)
    look = Look(
        fitted_font(MEDIUM, 12),
        fitted_font(BOLD, 12),
        fitted_font(MEDIUM, 11),
        14,
        2,
        5,
        frozenset({"approve", "failed"}),
    )
    graph(d, release, (0, 34, width - 1, height - 2), look)
    return img


# --- card: a rounded card with a black title bar ---------------------------------------------


def card(release: Release, others: int, now: datetime, width: int, height: int) -> Image.Image:
    """Draw the graph in a card whose title bar names the release, a job waiting for approval or failed in black."""
    img, d = _canvas(width, height)
    side = truncate_to_fit(aside(release, others, now), fitted_font(SANS_SEMI, 13), width * 0.5)
    room = width - 42 - fitted_font(SANS_SEMI, 13).getlength(side) - 16
    _card(d, 0, width, height, ROCKET, truncate_to_fit(_title(release), fitted_font(SANS_SEMI, 15), room), side)
    look = Look(
        fitted_font(SANS, 12),
        fitted_font(SANS_SEMI, 12),
        fitted_font(SANS, 11),
        13,
        1,
        4,
        frozenset({"approve", "failed"}),
    )
    graph(d, release, (10, CARD_BAR + 5, width - 11, height - 6), look)
    return img


STYLES: dict[str, Callable[[Release, int, datetime, int, int], Image.Image]] = {
    "airy": airy,
    "hero": hero,
    "card": card,
}
