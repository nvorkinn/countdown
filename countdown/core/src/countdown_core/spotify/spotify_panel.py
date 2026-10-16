import io
from typing import Literal

import requests
from PIL import Image, ImageDraw, ImageOps

from countdown_core.core.panel import Panel
from countdown_core.spotify.models import Album, Artist, Queue
from countdown_core.spotify.models import Image as AlbumImage
from countdown_core.spotify.spotify_styles import STYLES
from countdown_core.utils.utils import UBUNTU_BOLD_20, UBUNTU_MEDIUM_15, UBUNTU_MEDIUM_20, truncate_to_fit

# How the Spotify panel is drawn: the config's spotify.style.
Style = Literal["classic", "airy", "hero", "card"]


class SpotifyPanel(Panel):
    def __init__(self, queue: Queue, style: Style = "classic") -> None:
        self.queue = queue
        self.style = style

    def render(self, image_width: int, image_height: int) -> Image.Image:
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
                truncate_to_fit(f"Up next: {up_next}", UBUNTU_MEDIUM_15, space_remaining),
                "black",
                font=UBUNTU_MEDIUM_15,
                anchor="ld",
            )

        return img


def _create_album_image_panel(album: Album, height: int) -> Image.Image:
    """Downloads the album's art (see _pick_image) and scales it down to `height`. A local
    file has no art at all: that gets an empty (0-wide) image."""
    if not album.images:
        return Image.new("RGBA", (0, height))
    response = requests.get(str(_pick_image(album.images, height).url), timeout=10)
    response.raise_for_status()
    converted = Image.open(io.BytesIO(response.content)).convert("RGBA")
    converted.thumbnail((height, height))
    return converted


def _contrasty(art: Image.Image) -> Image.Image:
    """The cover in grey with its contrast stretched, so it still reads once it's dithered to
    black and white rather than turning to an even speckle."""
    if not art.size[0]:
        return art
    grey = ImageOps.autocontrast(art.convert("L"), cutoff=2)
    grey.putalpha(art.getchannel("A"))
    return grey.convert("RGBA")


def _pick_image(images: list[AlbumImage], height: int) -> AlbumImage:
    """The smallest image at least `height` tall, for thumbnail() to scale down (it never
    scales up, so a smaller one would stay small); the tallest if none is tall enough."""
    tall_enough = [image for image in images if image.height >= height]
    if tall_enough:
        return min(tall_enough, key=lambda image: image.height)
    return max(images, key=lambda image: image.height)


def _artists_names(artists: list[Artist]) -> str:
    return ", ".join(artist.name for artist in artists)
