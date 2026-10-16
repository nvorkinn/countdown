"""The Spotify area's now playing page: the cover, the track, and what's up next."""

import io
from typing import TYPE_CHECKING, Literal, override

import requests
from PIL import Image, ImageDraw, ImageOps

from countdown_core.core.panel import Panel
from countdown_core.i18n import _
from countdown_core.spotify.spotify_styles import STYLES
from countdown_core.utils.utils import UBUNTU_BOLD_20, UBUNTU_MEDIUM_15, UBUNTU_MEDIUM_20, truncate_to_fit

if TYPE_CHECKING:
    from countdown_core.spotify.models import Album, Artist, Queue
    from countdown_core.spotify.models import Image as AlbumImage
    from countdown_core.utils.clock import ClockFormat

# How the Spotify panel is drawn: the config's spotify.style.
Style = Literal["classic", "airy", "hero", "card"]


class SpotifyPanel(Panel):
    """What's playing, with its cover, and what's up next."""

    def __init__(self, queue: Queue, style: Style = "classic") -> None:
        """`queue`: the player's, with something playing; `style`: how to draw it."""
        self.queue = queue
        self.style = style

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        if self.style != "classic":
            album = self.queue.currently_playing.album
            return STYLES[self.style].now(
                self.queue, lambda size: _contrasty(_create_album_image_panel(album, size)), image_width, image_height
            )
        album_image_panel = _create_album_image_panel(self.queue.currently_playing.album, image_height)
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.font = UBUNTU_MEDIUM_20
        draw.fontmode = "1"

        img.paste(album_image_panel, (0, 0), album_image_panel)
        space_remaining = img.size[0] - album_image_panel.size[0] - 5
        draw.text(
            (album_image_panel.size[0] + 5, 0),
            truncate_to_fit(self.queue.currently_playing.name, UBUNTU_MEDIUM_20, space_remaining),
            "black",
            anchor="la",
        )
        draw.text(
            (album_image_panel.size[0] + 5, 27),
            truncate_to_fit(_artists_names(self.queue.currently_playing.artists), UBUNTU_BOLD_20, space_remaining),
            "black",
            font=UBUNTU_BOLD_20,
            anchor="la",
        )
        draw.text(
            (album_image_panel.size[0] + 5, 55),
            truncate_to_fit(self.queue.currently_playing.album.name, UBUNTU_MEDIUM_20, space_remaining),
            fill="black",
            anchor="la",
        )

        if self.queue.queue:
            up_next = f"{self.queue.queue[0].name} by {_artists_names(self.queue.queue[0].artists)}"
            draw.text(
                (album_image_panel.size[0] + 5, image_height - 10),
                truncate_to_fit(_("Up next: {track}").format(track=up_next), UBUNTU_MEDIUM_15, space_remaining),
                "black",
                font=UBUNTU_MEDIUM_15,
                anchor="ld",
            )

        return img


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


def _artists_names(artists: list[Artist]) -> str:
    return ", ".join(artist.name for artist in artists)
