"""The Spotify area's now playing page: the cover, the track, and what's up next."""

import io
from typing import TYPE_CHECKING, override

import requests
from PIL import Image, ImageOps

from countdown_core.core.panel import Panel
from countdown_core.spotify.spotify_styles import STYLES

if TYPE_CHECKING:
    from countdown_core.core.style import Style
    from countdown_core.spotify.models import Album, Queue
    from countdown_core.spotify.models import Image as AlbumImage
    from countdown_core.utils.clock import ClockFormat


class SpotifyPanel(Panel):
    """What's playing, with its cover, and what's up next."""

    def __init__(self, queue: Queue, style: Style = "hero") -> None:
        """`queue`: the player's, with something playing; `style`: how to draw it."""
        self.queue = queue
        self.style = style

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        album = self.queue.currently_playing.album
        return STYLES[self.style].now(
            self.queue, lambda size: _contrasty(_create_album_image_panel(album, size)), image_width, image_height
        )


def _create_album_image_panel(album: Album, height: int) -> Image.Image:
    """Download the album's art (see _pick_image) and scale it down to `height`.

    A local file has no art at all: that gets an empty (0-wide) image.
    """
    if not album.images:
        return Image.new("RGBA", (0, height))
    response = requests.get(str(_pick_image(album.images, height).url), timeout=10)
    response.raise_for_status()
    converted = Image.open(io.BytesIO(response.content)).convert("RGBA")
    converted.thumbnail((height, height))
    return converted


def _contrasty(art: Image.Image) -> Image.Image:
    """Turn the cover grey and stretch its contrast.

    So it still reads once it's dithered to black and white rather than turning to an even speckle.
    """
    if not art.size[0]:
        return art
    grey = ImageOps.autocontrast(art.convert("L"), cutoff=2)
    grey.putalpha(art.getchannel("A"))
    return grey.convert("RGBA")


def _pick_image(images: list[AlbumImage], height: int) -> AlbumImage:
    """Pick the smallest image at least `height` tall, or the tallest if none is tall enough.

    That's for thumbnail() to scale down: it never scales up, so a smaller one would stay small.
    """
    tall_enough = [image for image in images if image.height >= height]
    if tall_enough:
        return min(tall_enough, key=lambda image: image.height)
    return max(images, key=lambda image: image.height)
