import countdown_core  # noqa: F401, I001  (import order: see test_display_snapshots.py)
import io
import logging
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image as PILImage

from countdown_api import fixtures
from countdown_core.spotify import spotify_panel
from countdown_core.spotify.models import Album, Image, Playback, Queue, Track
from countdown_core.spotify.spotify_panel import SpotifyPanel, _album_art
from countdown_core.spotify.spotify_styles import STYLES
from countdown_core.store.music import ART_SIZE, art_image

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
def test_with_no_room_for_the_bar_only_the_length_is_drawn(symbols_font, style):
    """In a layout too narrow for a bar worth drawing the position changes nothing, as without one."""
    queue = Queue(currently_playing=playing(), queue=[playing("t2")])

    def narrow(seconds: float) -> PILImage.Image:
        panel = SpotifyPanel(queue, style, playback=position(), now=FETCHED_AT + timedelta(seconds=seconds))
        return panel.render(200, 120)

    assert same(narrow(0), narrow(90))


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
