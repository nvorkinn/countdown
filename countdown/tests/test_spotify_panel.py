import countdown_core  # noqa: F401, I001  (import order: see test_display_snapshots.py)
import io
import logging

import pytest
from PIL import Image as PILImage

from countdown_api import fixtures
from countdown_core.spotify import spotify_panel
from countdown_core.spotify.models import Album, Image
from countdown_core.spotify.spotify_panel import _album_art
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
