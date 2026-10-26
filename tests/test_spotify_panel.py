import countdown_renderer  # noqa: F401, I001  (import order: see test_display_snapshots.py)
import io
import logging
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image as PILImage

from countdown_api import fixtures
from countdown_renderer.spotify import spotify_panel
from countdown_shared.spotify.models import Album, Image, Playback, Queue, Track
from countdown_renderer.spotify.spotify_panel import HdSpotifyPanel, SpotifyPanel, _album_art, _paused
from countdown_renderer.spotify.spotify_styles import APPLE_MUSIC_LABELS, STYLES
from countdown_shared.store.music import ART_SIZE, art_image

ART = fixtures.read_bytes("music_art.jpg")


def image(height: int) -> Image:
    return Image(width=height, height=height, url=f"https://example.com/{height}.jpg")


def album(*images: Image) -> Album:
    return Album(name="An Album", images=list(images))


SPOTIFY_SIZES = [image(640), image(300), image(64)]  # what Spotify usually offers, widest first


# --- Which size is stored -------------------------------------------------------------


def test_the_stored_size_is_tall_enough_for_the_tallest_cover_drawn():
    """The Spotify area is 120 px high in every layout, and a style draws the cover at most that tall."""
    assert ART_SIZE == 120


def test_the_smallest_image_at_least_art_size_tall_is_the_stored_one():
    """The next size up, for thumbnail() to scale down: not the closer but smaller 64, nor the needlessly big 640."""
    assert art_image(album(*SPOTIFY_SIZES)).height == 300


def test_the_order_spotify_lists_them_in_does_not_matter():
    assert art_image(album(image(64), image(640), image(300))).height == 300


@pytest.mark.parametrize(("sizes", "stored"), [((64, 32), 64), ((120,), 120), ((300,), 300)])
def test_with_none_tall_enough_the_tallest_is_stored(sizes, stored):
    """Apple Music's one 300 px image, or an old album's small ones."""
    assert art_image(album(*(image(size) for size in sizes))).height == stored


def test_a_local_file_has_no_stored_cover():
    assert art_image(album()) is None


# --- Drawing it -----------------------------------------------------------------------


@pytest.mark.parametrize("height", [120, 78])
def test_the_stored_cover_is_scaled_down_to_the_styles_size(height):
    assert _album_art(ART, height).size == (height, height)


def test_a_small_cover_is_not_scaled_up():
    buf = io.BytesIO()
    PILImage.new("RGB", (64, 64), "grey").save(buf, format="PNG")

    assert _album_art(buf.getvalue(), 120).size == (64, 64)


def test_no_cover_draws_none():
    assert _album_art(None, 120).size == (0, 120)


def test_a_cover_that_wont_open_draws_none_rather_than_failing_the_page(monkeypatch):
    logger = logging.getLogger("test")
    warnings = []
    monkeypatch.setattr(spotify_panel, "logger", logger)
    monkeypatch.setattr(logger, "warning", lambda *args: warnings.append(args))

    assert _album_art(b"<html>not an image</html>", 120).size == (0, 120)
    assert _album_art(ART[: len(ART) // 2], 120).size == (0, 120)  # cut short
    assert len(warnings) == 2


# --- The progress bar ---------------------------------------------------------------------

FETCHED_AT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
DURATION = timedelta(minutes=3)


def playing(track_id: str | None = "t1") -> Track:
    return Track.model_validate(
        {
            "id": track_id,
            "name": "A Song",
            "artists": [{"name": "An Artist"}],
            "album": {"name": "An Album", "images": []},
            "duration_ms": 180_000,
        }
    )


def position(progress_ms: int = 60_000, *, is_playing: bool = True, track_id: str | None = "t1") -> Playback:
    return Playback(fetched_at=FETCHED_AT, is_playing=is_playing, progress_ms=progress_ms, track_id=track_id)


def drawn(style: str, playback: Playback | None, seconds: float = 0, track_id: str | None = "t1") -> PILImage.Image:
    """Draw the playing page at `seconds` after the position was fetched."""
    queue = Queue(currently_playing=playing(track_id), queue=[playing("t2")])
    panel = SpotifyPanel(queue, style, playback=playback, now=FETCHED_AT + timedelta(seconds=seconds))
    return panel.render(536, 120)


def same(a: PILImage.Image, b: PILImage.Image) -> bool:
    return a.tobytes() == b.tobytes()


@pytest.mark.parametrize("style", STYLES)
def test_the_position_advances_with_now_while_playing(symbols_font, style):
    start, later = drawn(style, position(), 0), drawn(style, position(), 30)

    assert not same(start, later)
    assert same(later, drawn(style, position(), 30))  # the same inputs draw the same frame


@pytest.mark.parametrize("style", STYLES)
def test_the_bar_is_drawn_only_with_a_position(symbols_font, style):
    assert not same(drawn(style, position()), drawn(style, None))


@pytest.mark.parametrize("style", STYLES)
def test_the_position_is_clamped_at_the_tracks_length(symbols_font, style):
    """A track that ended since the fetch (the next one not fetched yet) shows a full bar, not one past the end."""
    at_the_end = drawn(style, position(), 120)  # 60 s in, 120 s on: exactly 3:00

    assert same(at_the_end, drawn(style, position(), 3600))
    assert not same(at_the_end, drawn(style, position(), 100))


@pytest.mark.parametrize("style", STYLES)
def test_a_paused_track_shows_a_static_bar_at_its_position(symbols_font, style):
    paused = position(is_playing=False)

    assert same(drawn(style, paused, 0), drawn(style, paused, 90))
    assert not same(drawn(style, paused), drawn(style, None))
    assert not same(drawn(style, paused, 90), drawn(style, position(), 90))


@pytest.mark.parametrize("style", STYLES)
def test_a_position_of_another_track_draws_no_bar(symbols_font, style):
    """Just after a skip the queue may still be the old track's: no bar rather than a wrong one."""
    assert same(drawn(style, position(track_id="other"), 30), drawn(style, None, 30))


@pytest.mark.parametrize("style", STYLES)
def test_a_track_without_an_id_draws_no_bar(symbols_font, style):
    """Two local files both have a null id, so nothing says the position is this one's."""
    assert same(drawn(style, position(track_id=None), 30, track_id=None), drawn(style, None, 30, track_id=None))


@pytest.mark.parametrize("style", STYLES)
def test_without_a_position_the_page_is_drawn_as_it_was(symbols_font, style):
    queue = Queue(currently_playing=playing(), queue=[playing("t2")])
    before = STYLES[style].now(queue, lambda size: PILImage.new("RGBA", (0, size)), 536, 120)  # no position argument

    assert same(drawn(style, None), before)


def test_the_position_is_the_one_read_plus_the_time_since():
    assert position(61_234).elapsed(playing(), FETCHED_AT + timedelta(seconds=10)) == timedelta(
        seconds=71, milliseconds=234
    )


def test_a_paused_position_does_not_move():
    assert position(61_234, is_playing=False).elapsed(playing(), FETCHED_AT + timedelta(hours=1)) == timedelta(
        seconds=61, milliseconds=234
    )


def test_the_position_is_clamped_to_zero_and_to_the_length():
    """A render clock behind the fetch's, and a track that has ended since, can't take it outside the track."""
    assert position(1_000).elapsed(playing(), FETCHED_AT - timedelta(seconds=30)) == timedelta(0)
    assert position(179_000).elapsed(playing(), FETCHED_AT + timedelta(minutes=5)) == DURATION
    assert position(200_000, is_playing=False).elapsed(playing(), FETCHED_AT) == DURATION


def test_a_position_of_another_track_is_none():
    assert position(track_id="other").elapsed(playing(), FETCHED_AT) is None
    assert position(track_id=None).elapsed(playing(), FETCHED_AT) is None
    assert position().elapsed(playing(track_id=None), FETCHED_AT) is None


# --- Is it playing, is it paused ----------------------------------------------------------


def test_playing_now_is_a_recent_position_of_this_track_that_was_playing():
    track = playing()
    just_after = FETCHED_AT + timedelta(seconds=30)

    assert position().is_playing_now(track, just_after)
    assert not position(is_playing=False).is_playing_now(track, just_after)
    assert not position(track_id="other").is_playing_now(track, just_after)
    assert not position().is_playing_now(playing(None), just_after)
    # One read too long ago to say it is still playing, or by a clock that is behind the provider's.
    assert not position().is_playing_now(track, FETCHED_AT + timedelta(minutes=5))
    assert not position().is_playing_now(track, FETCHED_AT - timedelta(seconds=5))


def test_paused_is_a_position_of_this_track_that_was_not_playing():
    track = playing()

    assert position(is_playing=False).is_paused(track)
    assert not position().is_paused(track)
    assert not position(is_playing=False, track_id="other").is_paused(track)
    assert not position(is_playing=False, track_id=None).is_paused(playing(None))


# --- The progress bar along the bottom ----------------------------------------------------

# Where each style draws the bar in a 536 x 120 page: from, to (exclusive) and its bottom row.
BAR = {"airy": (0, 536, 119), "hero": (0, 536, 119), "card": (8, 528, 113)}


def dark(image, x, y):
    """Whether the page has a dark pixel at (x, y): black, or the bar's ink on the card's black bar."""
    r, g, b, a = image.getpixel((x, y))
    return a > 0 and r + g + b < 3 * 64


@pytest.mark.parametrize("style", STYLES)
def test_the_bar_spans_the_page_along_the_bottom_and_fills_as_far_as_the_position(symbols_font, style):
    left, right, bottom = BAR[style]
    image = drawn(style, position(90_000), 0)  # 90 s of the 3 minutes: half

    # Its outline is dark from end to end along the bottom, and not beyond either end.
    assert all(dark(image, x, bottom) for x in range(left, right))
    if left:
        assert not dark(image, left - 3, bottom)
    # The played half is solid, the rest not: in the middle of the bar, a quarter and three quarters along.
    middle = bottom - 3
    assert dark(image, left + (right - left) // 4, middle)
    assert not dark(image, left + 3 * (right - left) // 4, middle)


@pytest.mark.parametrize("style", STYLES)
def test_the_bar_fills_with_the_position(symbols_font, style):
    left, right, bottom = BAR[style]
    middle = bottom - 3

    def filled_to(progress_ms):
        image = drawn(style, position(progress_ms), 0)
        return max(x for x in range(left, right - 2) if dark(image, x, middle))

    assert filled_to(0) < left + 3
    assert filled_to(90_000) == pytest.approx(left + (right - 1 - left) / 2, abs=2)
    assert filled_to(180_000) >= right - 3


def with_cover(style, playback, seconds=0):
    queue = Queue(currently_playing=playing(), queue=[playing("t2")])
    panel = SpotifyPanel(queue, style, art=ART, playback=playback, now=FETCHED_AT + timedelta(seconds=seconds))
    return panel.render(536, 120)


@pytest.mark.parametrize("style", ["airy", "hero"])
def test_the_bar_starts_right_under_the_cover(symbols_font, style):
    """With a bar the cover is drawn smaller, and the bar starts under its bottom left corner with no gap."""
    with_bar, without = with_cover(style, position(90_000)), with_cover(style, None)

    assert without.getpixel((2, 118))[3] == 255  # without a bar the cover reaches the bottom
    assert with_bar.getpixel((2, 112))[3] == 255  # with one, its last row is the one above the bar
    assert dark(with_bar, 2, 113)  # the played part, from the bar's top row
    assert dark(with_bar, 2, 119)  # to the page's bottom row


@pytest.mark.parametrize("style", STYLES)
def test_the_unplayed_part_of_the_bar_is_only_a_line_along_the_bottom(symbols_font, style):
    """No box round the bar: past the position there is nothing but the bottom line, and no edge at the end."""
    left, right, bottom = BAR[style]
    image = drawn(style, position(90_000), 0)
    unplayed = left + 3 * (right - left) // 4

    assert dark(image, unplayed, bottom)  # the line
    assert not any(dark(image, unplayed, y) for y in range(bottom - 6, bottom))  # and nothing above it
    assert not any(dark(image, right - 1, y) for y in range(bottom - 6, bottom))  # no right edge


# --- A paused cover -----------------------------------------------------------------------


def test_a_paused_cover_is_faded_with_a_pause_symbol_over_it():
    cover = PILImage.new("RGBA", (100, 100), (100, 100, 100, 255))

    paused = _paused(cover)

    assert paused.size == cover.size
    assert paused.getpixel((2, 2)) == (193, 193, 193, 255)  # 60% of the way to white
    # Two black bars either side of the middle, each in a white halo, with the faded cover showing between them.
    assert paused.getpixel((35, 50)) == (0, 0, 0, 255)
    assert paused.getpixel((65, 50)) == (0, 0, 0, 255)
    assert paused.getpixel((44, 50)) == (255, 255, 255, 255)  # the halo beside the left bar
    assert paused.getpixel((50, 50)) == (193, 193, 193, 255)  # between the halos
    assert paused.getpixel((35, 20)) == (193, 193, 193, 255)  # above the bars


def test_a_page_without_a_cover_has_nothing_to_fade():
    nothing = PILImage.new("RGBA", (0, 120))

    assert _paused(nothing) is nothing


@pytest.mark.parametrize("style", STYLES)
def test_a_paused_track_draws_a_faded_cover_and_a_playing_one_does_not(symbols_font, style):
    playing_page = with_cover(style, position())
    paused_page = with_cover(style, position(is_playing=False))

    assert not same(playing_page, paused_page)
    # Only the cover (and the time, which stops) differ: what's beside it is the same.
    assert same(playing_page.crop((200, 30, 536, 90)), paused_page.crop((200, 30, 536, 90)))


@pytest.mark.parametrize("style", STYLES)
def test_a_position_of_another_track_leaves_the_cover_as_it_was(symbols_font, style):
    """Not known to be paused, so no pause symbol, and no bar either."""
    other = position(is_playing=False, track_id="other")

    assert same(with_cover(style, other), with_cover(style, None))


def test_the_hd_panel_is_made_from_the_pages_parts():
    queue = Queue(currently_playing=playing(), queue=[playing("t2")])
    panel = SpotifyPanel(queue, "card", APPLE_MUSIC_LABELS, b"art", position(), FETCHED_AT)

    hd = HdSpotifyPanel.of(panel)

    assert (hd.queue, hd.style, hd.labels, hd.art, hd.playback, hd.now) == (
        queue,
        "card",
        APPLE_MUSIC_LABELS,
        b"art",
        panel.playback,
        FETCHED_AT,
    )


@pytest.mark.parametrize("style", STYLES)
def test_the_hd_panel_draws_its_styles_wide_page(symbols_font, style):
    queue = Queue(currently_playing=playing(), queue=[playing("t2")])
    hd = HdSpotifyPanel(queue, style, playback=position(), now=FETCHED_AT + timedelta(seconds=30))

    assert same(hd.render(692, 150), STYLES[style].wide(queue, hd._cover, 692, 150, hd.labels, timedelta(seconds=90)))
    assert not same(hd.render(692, 150), SpotifyPanel(queue, style).render(692, 150))
