"""The looks the Spotify panel can have (the config's style).

Each matches the bus/tube style of the same name. A style draws both pages: what's playing, and the top tracks and
artists. Each draws into `width` x `height`, on a transparent background.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from countdown_core.i18n import N_, _
from countdown_core.tfl.arrival_styles import BOLD, MEDIUM, REGULAR, SANS, SANS_SEMI, _canvas, _shrunk
from countdown_core.utils.utils import fitted_font, google_symbols, resized, truncate_to_fit

if TYPE_CHECKING:
    from datetime import timedelta

    from countdown_core.spotify.models import Artist, Queue, Track

# Material Symbols glyphs
NOTE = chr(0xE405)
MIC = chr(0xE029)
SKIP_NEXT = chr(0xE044)
HISTORY = chr(0xE889)

# The cover at a given size: a square, or 0 wide if the track has none (a local file).
AlbumArt = Callable[[int], Image.Image]


@dataclass(frozen=True)
class Labels:
    """What the now page calls its two parts, which differ by service: English, marked with N_()."""

    now: str  # the track with the cover
    next: str  # the one under it
    badge: str  # the hero style's short badge for that one
    next_icon: str  # its Material Symbols glyph


SPOTIFY_LABELS = Labels(N_("Now playing"), N_("Up next"), N_("NEXT"), SKIP_NEXT)
# Apple Music can't say what's playing or what's next, only what was played: the last track, and the one before it.
APPLE_MUSIC_LABELS = Labels(N_("Last played"), N_("Earlier"), N_("EARLIER"), HISTORY)


# The headings of the two top lists, by kind.
TOP = {"tracks": N_("Top tracks"), "artists": N_("Top artists")}


def _aside(column: int, period: str, service: str | None) -> str:
    """Return what a top page's `column` (0 tracks, 1 artists) says beside its heading, translated.

    The period; but when the area shows both services, the tracks' side says whose lists they are instead.
    """
    return _(service) if service and column == 0 else _(period)


def _artists(track: Track) -> str:
    return ", ".join(artist.name for artist in track.artists)


def _length(duration: timedelta) -> str:
    """7:07, or 1:02:03 for anything an hour or longer."""
    minutes, seconds = divmod(round(duration.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def _track_and_by(  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, and in which fonts
    d: ImageDraw.ImageDraw,
    y: float,
    left: float,
    right: float,
    name: str,
    by: str,
    name_font: ImageFont.FreeTypeFont,
    by_font: ImageFont.FreeTypeFont,
) -> None:
    """Draw `name` from the left and, when there is one, `by` against the right, smaller.

    The artist gets at most 40% of the row, so a long one can't squeeze the name out.
    """
    if by:
        by = truncate_to_fit(by, by_font, (right - left) * 0.4)
        d.text((right, y + 1), by, "black", by_font, anchor="rm")
        right -= by_font.getlength(by) + 10
    d.text((left, y), truncate_to_fit(name, name_font, right - left), "black", name_font, anchor="lm")


def _columns(tracks: list[Track], artists: list[Artist]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Put the two lists side by side, as (name, by) rows; an artist has no "by"."""
    return [
        ("tracks", [(track.name, track.artists[0].name if track.artists else "") for track in tracks]),
        ("artists", [(artist.name, "") for artist in artists]),
    ]


# --- airy: small-caps labels and a hairline, like the weather -------------------------------


def airy_label(d: ImageDraw.ImageDraw, x: float, y: float, icon: str, text: str) -> float:
    """Draw a small icon and a small-caps label after it, centred on `y`; return where the label ends."""
    d.text((x, y), icon, "black", resized(google_symbols(), 15), anchor="lm")
    label = fitted_font(SANS_SEMI, 11)
    d.text((x + 18, y), text, "black", label, anchor="lm")
    return x + 18 + label.getlength(text)


def airy_now(queue: Queue, art: AlbumArt, width: int, height: int, labels: Labels = SPOTIFY_LABELS) -> Image.Image:
    """Draw what's playing beside its cover, under a small-caps label, with what's up next below a hairline."""
    img, d = _canvas(width, height)
    track = queue.currently_playing
    cover = art(height)
    img.paste(cover, (0, 0), cover)
    x = cover.size[0] + 18 if cover.size[0] else 0
    room = width - x
    label = fitted_font(SANS_SEMI, 11)
    airy_label(d, x, 12, NOTE, _(labels.now).upper())
    d.text((width, 12), _length(track.duration_ms), "black", label, anchor="rm")
    title = _shrunk(track.name, SANS, 32, room)
    d.text((x, 48), truncate_to_fit(track.name, title, room), "black", title, anchor="ls")
    subtitle = fitted_font(SANS, 17)
    d.text(
        (x, 72),
        truncate_to_fit(f"{_artists(track)} · {track.album.name}", subtitle, room),
        "black",
        subtitle,
        anchor="ls",
    )
    if queue.queue:
        d.line((x, 86, width, 86), fill="black")
        next_x = airy_label(d, x, 104, labels.next_icon, _(labels.next).upper()) + 10
        up_next, body = queue.queue[0], fitted_font(SANS, 15)
        text = truncate_to_fit(f"{up_next.name} · {_artists(up_next)}", body, width - next_x)
        d.text((next_x, 104), text, "black", body, anchor="lm")
    return img


def airy_top(  # noqa: PLR0913, PLR0917 -- every style's top page takes the same arguments
    period: str, tracks: list[Track], artists: list[Artist], width: int, height: int, service: str | None = None
) -> Image.Image:
    """Draw the two top lists side by side, each under a small-caps label and a hairline."""
    img, d = _canvas(width, height)
    label = fitted_font(SANS_SEMI, 11)
    rank_font, name_font, by_font = fitted_font(SANS_SEMI, 14), fitted_font(SANS, 14), fitted_font(SANS, 12)
    gap = 24
    column = (width - gap) / 2
    for i, (kind, rows) in enumerate(_columns(tracks, artists)):
        left = i * (column + gap)
        right = left + column
        airy_label(d, left, 8, NOTE if kind == "tracks" else MIC, _(TOP[kind]).upper())
        if i == 1 or service:
            aside = _aside(i, _("LAST {period}").format(period=_(period)), service)
            d.text((right, 8), aside.upper(), "black", label, anchor="rm")
        d.line((left, 19, right, 19), fill="black")
        y = 31
        for rank, (name, by) in enumerate(rows, 1):
            if y + 9 > height:
                break
            d.text((left + 8, y), str(rank), "black", rank_font, anchor="mm")
            _track_and_by(d, y, left + 22, right, name, by, name_font, by_font)
            y += 19
    return img


# --- hero: an icon header over a heavy rule, and number one big, like the next bus ----------


def _hero_header(d: ImageDraw.ImageDraw, left: float, right: float, icon: str, title: str, aside: str) -> None:  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, and with which icon
    d.text((left, 11), icon, "black", resized(google_symbols(), 22), anchor="lm")
    d.text((left + 26, 11), title, "black", fitted_font(SANS_SEMI, 17), anchor="lm")
    d.text((right, 11), aside, "black", fitted_font(MEDIUM, 13), anchor="rm")
    d.line((left, 26, right, 26), fill="black", width=2)


def hero_now(queue: Queue, art: AlbumArt, width: int, height: int, labels: Labels = SPOTIFY_LABELS) -> Image.Image:
    """Draw what's playing beside its cover, the title big, with what's up next in a NEXT badge's row."""
    img, d = _canvas(width, height)
    track = queue.currently_playing
    cover = art(height)
    img.paste(cover, (0, 0), cover)
    x = cover.size[0] + 14 if cover.size[0] else 0
    room = width - x
    _hero_header(d, x, width, NOTE, _(labels.now), _length(track.duration_ms))
    title = _shrunk(track.name, BOLD, 26, room)
    d.text((x, 47), truncate_to_fit(track.name, title, room), "black", title, anchor="lm")
    subtitle = fitted_font(REGULAR, 14)
    d.text(
        (x, 70),
        truncate_to_fit(f"{_artists(track)} — {track.album.name}", subtitle, room),
        "black",
        subtitle,
        anchor="lm",
    )
    if queue.queue:
        y = 92
        badge, badge_font = _(labels.badge), fitted_font(BOLD, 12)
        # 46 wide, as NEXT has always been; a longer word widens it.
        badge_width = max(46, round(badge_font.getlength(badge)) + 12)
        d.rounded_rectangle((x, y, x + badge_width, y + 20), 4, outline="black", width=2)
        d.text((x + badge_width / 2, y + 10), badge, "black", badge_font, anchor="mm")
        up_next = queue.queue[0]
        _track_and_by(
            d,
            y + 10,
            x + badge_width + 8,
            width,
            up_next.name,
            _artists(up_next),
            fitted_font(MEDIUM, 14),
            fitted_font(BOLD, 14),
        )
    return img


def hero_top(  # noqa: PLR0913, PLR0917 -- every style's top page takes the same arguments
    period: str, tracks: list[Track], artists: list[Artist], width: int, height: int, service: str | None = None
) -> Image.Image:
    """Draw the two top lists side by side, number one big in a black badge and two to five under it."""
    img, d = _canvas(width, height)
    gap = 24
    column = (width - gap) / 2
    rank_font, name_font = fitted_font(BOLD, 12), fitted_font(MEDIUM, 13)
    for i, (kind, rows) in enumerate(_columns(tracks, artists)):
        left = round(i * (column + gap))
        right = round(left + column)
        _hero_header(
            d,
            left,
            right,
            NOTE if kind == "tracks" else MIC,
            _(TOP[kind]),
            _aside(i, period, service) if i == 1 or service else "",
        )
        if not rows:
            continue
        # Number one big, in a black badge like the next departure's route.
        name, by = rows[0]
        y = 34
        d.rounded_rectangle((left, y, left + 40, y + 40), 6, fill="black")
        d.text((left + 20, y + 20), "1", "white", fitted_font(BOLD, 24), anchor="mm")
        room = right - left - 48
        font = _shrunk(name, BOLD, 18, room)
        d.text((left + 48, y + 12 if by else y + 20), truncate_to_fit(name, font, room), "black", font, anchor="lm")
        if by:
            by_font = fitted_font(REGULAR, 12)
            d.text((left + 48, y + 31), truncate_to_fit(by, by_font, room), "black", by_font, anchor="lm")
        # Two to five under it, two to a line, each with its rank in an outlined badge.
        for n, (name, _by) in enumerate(rows[1:5]):
            cell_x = left + (n % 2) * (column / 2 + 2)
            cell_y = 84 + (n // 2) * 19
            if cell_y + 16 > height:
                break
            d.rounded_rectangle((cell_x, cell_y, cell_x + 18, cell_y + 16), 3, outline="black", width=2)
            d.text((cell_x + 9, cell_y + 8), str(n + 2), "black", rank_font, anchor="mm")
            d.text(
                (cell_x + 24, cell_y + 8),
                truncate_to_fit(name, name_font, column / 2 - 30),
                "black",
                name_font,
                anchor="lm",
            )
    return img


# --- card: a rounded card with a black title bar, like the card arrivals ---------------------

CARD_BAR = 28


def _card(d: ImageDraw.ImageDraw, left: int, right: int, height: int, icon: str, title: str, aside: str) -> None:  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, and with which icon
    d.rounded_rectangle((left, 0, right - 1, height - 1), 10, fill="white", outline="black", width=2)
    d.rounded_rectangle((left, 0, right - 1, CARD_BAR), 10, fill="black")
    d.rectangle((left, CARD_BAR - 10, right - 1, CARD_BAR), fill="black")  # square off the bar's bottom corners
    d.text((left + 10, 14), icon, "white", resized(google_symbols(), 18), anchor="lm")
    d.text((left + 32, 14), title, "white", fitted_font(SANS_SEMI, 15), anchor="lm")
    d.text((right - 11, 14), aside, "white", fitted_font(SANS_SEMI, 13), anchor="rm")


def card_now(queue: Queue, art: AlbumArt, width: int, height: int, labels: Labels = SPOTIFY_LABELS) -> Image.Image:
    """Draw what's playing in a card, its cover inside, with what's up next under a dotted line."""
    img, d = _canvas(width, height)
    track = queue.currently_playing
    _card(d, 0, width, height, NOTE, _(labels.now), _length(track.duration_ms))
    cover = art(height - CARD_BAR - 14)
    img.paste(cover, (8, CARD_BAR + 7), cover)
    x = 8 + cover.size[0] + 12 if cover.size[0] else 10
    room = width - x - 10
    title = _shrunk(track.name, BOLD, 20, room)
    d.text((x, 47), truncate_to_fit(track.name, title, room), "black", title, anchor="lm")
    subtitle = fitted_font(SANS, 15)
    d.text(
        (x, 69),
        truncate_to_fit(f"{_artists(track)} · {track.album.name}", subtitle, room),
        "black",
        subtitle,
        anchor="lm",
    )
    if queue.queue:
        for dot in range(x, width - 10, 4):
            d.point((dot, 84), fill="black")
        d.text((x, 100), labels.next_icon, "black", resized(google_symbols(), 18), anchor="lm")
        up_next, body = queue.queue[0], fitted_font(SANS, 14)
        text = truncate_to_fit(f"{up_next.name} · {_artists(up_next)}", body, room - 22)
        d.text((x + 22, 100), text, "black", body, anchor="lm")
    return img


def card_top(  # noqa: PLR0913, PLR0917 -- every style's top page takes the same arguments
    period: str, tracks: list[Track], artists: list[Artist], width: int, height: int, service: str | None = None
) -> Image.Image:
    """Draw the two top lists side by side, a card each."""
    img, d = _canvas(width, height)
    gap = 10
    column = (width - gap) / 2
    rank_font, name_font, by_font = fitted_font(BOLD, 14), fitted_font(SANS, 14), fitted_font(SANS, 12)
    for i, (kind, rows) in enumerate(_columns(tracks, artists)):
        left = round(i * (column + gap))
        right = round(left + column)
        _card(d, left, right, height, NOTE if kind == "tracks" else MIC, _(TOP[kind]), _aside(i, period, service))
        y = CARD_BAR + 13
        for rank, (name, by) in enumerate(rows, 1):
            if y + 7 > height - 2:
                break
            d.text((left + 18, y), str(rank), "black", rank_font, anchor="rm")
            _track_and_by(d, y, left + 26, right - 11, name, by, name_font, by_font)
            y += 17
    return img


@dataclass(frozen=True)
class Style:
    """A Spotify style's two pages: `now` draws what's playing, `top` the top tracks and artists."""

    now: Callable[[Queue, AlbumArt, int, int, Labels], Image.Image]
    top: Callable[[str, list[Track], list[Artist], int, int, str | None], Image.Image]


STYLES: dict[str, Style] = {
    "airy": Style(airy_now, airy_top),
    "hero": Style(hero_now, hero_top),
    "card": Style(card_now, card_top),
}
