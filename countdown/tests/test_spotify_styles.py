import countdown_core  # noqa: F401, I001  (import order: see test_display_snapshots.py)
from datetime import timedelta

import pytest
from PIL import Image

from countdown_core.spotify.models import Artist, Queue, Track
from countdown_core.spotify.spotify_styles import STYLES, _length

STYLE_NAMES = list(STYLES)


def _track(name: str, artist: str = "An Artist") -> Track:
    return Track.model_validate(
        {"name": name, "artists": [{"name": artist}], "album": {"name": "An Album", "images": []}, "duration_ms": 1}
    )


def _no_art(size: int) -> Image.Image:
    return Image.new("RGBA", (0, size))


def _clear_art(size: int) -> Image.Image:
    """Make a cover that takes up room but draws nothing, so only the text shows where it went."""
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def _square_art(size: int) -> Image.Image:
    return Image.new("RGBA", (size, size), (0, 0, 0, 255))


def _inked(img: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """Whether anything dark is drawn in `box` (the card's white fill doesn't count)."""
    on_white = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("L")
    return on_white.crop(box).getextrema()[0] < 128


def _left_edge(img: Image.Image, box: tuple[int, int, int, int]) -> int:
    """How far into `box` the first dark pixel is."""
    on_white = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("L")
    return on_white.crop(box).point(lambda v: 255 if v < 128 else 0).getbbox()[0]


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_a_track_without_art_starts_its_text_at_the_left(style):
    """A local file has no cover: the text takes its place rather than leaving a gap."""
    queue = Queue(currently_playing=_track("A Song"), queue=[])
    with_art = STYLES[style].now(queue, _clear_art, 536, 120)
    without = STYLES[style].now(queue, _no_art, 536, 120)

    title_row = (4, 40, 536, 55)  # clear of the card's outline
    assert _left_edge(without, title_row) < _left_edge(with_art, title_row)


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_nothing_up_next_leaves_the_bottom_row_empty(style):
    playing = _track("A Song")
    last = STYLES[style].now(Queue(currently_playing=playing, queue=[]), _no_art, 536, 120)
    more = STYLES[style].now(Queue(currently_playing=playing, queue=[_track("Next")]), _no_art, 536, 120)

    bottom = (20, 90, 520, 112)
    assert not _inked(last, bottom)
    assert _inked(more, bottom)


@pytest.mark.parametrize("style", STYLE_NAMES)
@pytest.mark.parametrize("width", [470, 536, 790])
def test_long_names_stay_inside_the_panel(style, width):
    long = "A Very Long Song Title That Goes On And On And On Forever (Extended Remix)"
    queue = Queue(currently_playing=_track(long, long), queue=[_track(long, long)])
    tracks = [_track(long, long)] * 5
    artists = [Artist(name=long)] * 5

    for img in (
        STYLES[style].now(queue, _square_art, width, 120),
        STYLES[style].top("12 months", tracks, artists, width, 120),
    ):
        assert img.size == (width, 120)
        assert img.getchannel("A").getbbox()[2] <= width


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_empty_top_lists_draw_just_the_headers(style):
    img = STYLES[style].top("4 weeks", [], [], 536, 120)

    assert img.getchannel("A").getbbox() is not None


@pytest.mark.parametrize(
    ("duration", "shown"),
    [
        (timedelta(minutes=7, seconds=7), "7:07"),
        (timedelta(seconds=59), "0:59"),
        (timedelta(hours=1, seconds=3), "1:00:03"),
    ],
)
def test_a_tracks_length_reads_like_a_clock(duration, shown):
    assert _length(duration) == shown
