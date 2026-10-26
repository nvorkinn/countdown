"""The Spotify area's now playing page: the cover, the track, and what's up next (for Apple Music, last played)."""

import io
import logging
from typing import TYPE_CHECKING, Self, override

from PIL import Image, ImageDraw, ImageOps

from countdown_renderer import moment
from countdown_renderer.panel import Panel
from countdown_renderer.spotify.spotify_styles import SPOTIFY_LABELS, STYLES

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from countdown_core.spotify.models import Playback, Queue
    from countdown_renderer.spotify.spotify_styles import Labels
    from countdown_renderer.style import Style
    from countdown_renderer.utils.clock import ClockFormat

logger = logging.getLogger(__name__)

# A paused cover is faded this far towards white, with a pause symbol (two bars) over it this big, as shares of the
# cover's side.
PAUSED_FADE = 0.6
PAUSE_BAR_WIDTH = 0.15
PAUSE_BAR_HEIGHT = 0.5
PAUSE_BAR_GAP = 0.14
# The white edge round each bar, in pixels, so the symbol reads over what is left of the picture.
PAUSE_HALO = 3


class SpotifyPanel(Panel):
    """What's playing, with its cover, and what's up next."""

    scales = True

    def __init__(  # noqa: PLR0913, PLR0917 -- the page's parts, each optional after the queue
        self,
        queue: Queue,
        style: Style = "hero",
        labels: Labels = SPOTIFY_LABELS,
        art: bytes | None = None,
        playback: Playback | None = None,
        now: datetime | None = None,
    ) -> None:
        """`queue`: the player's, with something playing; `style`: how to draw it; `labels`: what to call its parts.

        Apple Music's (APPLE_MUSIC_LABELS) has the last played track in place of what's playing, and the ones before
        it in place of what's next. `art`: the playing track's cover, an image file's bytes as the store holds it
        (keys.album_art); None draws the page without one, as for a local file, which has none.

        `playback`: where Spotify's player was in the track (None for Apple Music, and when the store has none). The
        page draws a progress bar from it as of `now` (default: the render's, core.moment), but only if it is the
        playing track's position; a position of another track draws no bar rather than a wrong one. A paused track's
        cover is faded, with a pause symbol over it.
        """
        self.queue = queue
        self.style = style
        self.labels = labels
        self.art = art
        self.playback = playback
        self.now = now

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        return STYLES[self.style].now(
            self.queue,
            self._cover,
            image_width,
            image_height,
            self.labels,
            self._elapsed(),
        )

    def _cover(self, size: int) -> Image.Image:
        """Return the cover at `size`: contrasty, and faded with a pause symbol if the player is paused."""
        cover = _contrasty(_album_art(self.art, size))
        track = self.queue.currently_playing
        if self.playback is not None and track is not None and self.playback.is_paused(track):
            return _paused(cover)
        return cover

    def _elapsed(self) -> timedelta | None:
        """How far into the playing track the player is at `now`; None if there's no position to draw."""
        track = self.queue.currently_playing
        if self.playback is None or track is None:
            return None
        return self.playback.elapsed(track, self.now or moment.now())


class HdSpotifyPanel(SpotifyPanel):
    """The now page as the HD screen shows it, wide: what's playing in one cell and what's up next in another."""

    @classmethod
    def of(cls, panel: SpotifyPanel) -> Self:
        """Return `panel`'s page, drawn the HD screen's way."""
        return cls(panel.queue, panel.style, panel.labels, panel.art, panel.playback, panel.now)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        return STYLES[self.style].wide(
            self.queue,
            self._cover,
            image_width,
            image_height,
            self.labels,
            self._elapsed(),
        )


def _album_art(art: bytes | None, height: int) -> Image.Image:
    """Open the cover and scale it down to `height` (it's stored at least as tall as any style draws it).

    No cover, or one that won't open, gets an empty (0-wide) image, and the page is drawn without it.
    """
    if art:
        try:
            cover = Image.open(io.BytesIO(art)).convert("RGBA")
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            logger.warning("Couldn't open the album's cover (%s); drawing the page without it", e)
        else:
            # One bicubic pass, as before Pillow 13 began reducing RGBA in steps first (a softer cover, whose
            # white areas then dither to speckle).
            cover.thumbnail((height, height), reducing_gap=None)
            return cover
    return Image.new("RGBA", (0, height))


def _contrasty(art: Image.Image) -> Image.Image:
    """Turn the cover grey and stretch its contrast.

    So it still reads once it's dithered to black and white rather than turning to an even speckle.
    """
    if not art.size[0]:
        return art
    grey = ImageOps.autocontrast(art.convert("L"), cutoff=2)
    grey.putalpha(art.getchannel("A"))
    return grey.convert("RGBA")


def _paused(cover: Image.Image) -> Image.Image:
    """Fade the cover towards white and draw a pause symbol (two vertical bars) over its middle.

    The bars are solid black with a white edge, so they read once the page is dithered to black and white. A page
    without a cover (0 wide) is left as it is.
    """
    side = cover.size[0]
    if not side:
        return cover
    white = Image.new("RGBA", cover.size, "white")
    faded = Image.blend(Image.alpha_composite(white, cover), white, PAUSED_FADE)
    faded.putalpha(cover.getchannel("A"))
    width, height, gap = (round(side * share) for share in (PAUSE_BAR_WIDTH, PAUSE_BAR_HEIGHT, PAUSE_BAR_GAP))
    left = (side - (2 * width + gap)) // 2
    top = (cover.size[1] - height) // 2
    d = ImageDraw.Draw(faded)
    for x in (left, left + width + gap):
        d.rectangle(
            (x - PAUSE_HALO, top - PAUSE_HALO, x + width - 1 + PAUSE_HALO, top + height - 1 + PAUSE_HALO), fill="white"
        )
    for x in (left, left + width + gap):
        d.rectangle((x, top, x + width - 1, top + height - 1), fill="black")
    return faded
