"""The Spotify area's now playing page: the cover, the track, and what's up next (for Apple Music, last played)."""

import io
import logging
from typing import TYPE_CHECKING, override

from PIL import Image, ImageOps

from countdown_core.core import moment
from countdown_core.core.panel import Panel
from countdown_core.spotify.spotify_styles import SPOTIFY_LABELS, STYLES

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from countdown_core.core.style import Style
    from countdown_core.spotify.models import Playback, Queue
    from countdown_core.spotify.spotify_styles import Labels
    from countdown_core.utils.clock import ClockFormat

logger = logging.getLogger(__name__)


class SpotifyPanel(Panel):
    """What's playing, with its cover, and what's up next."""

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
        playing track's position; a position of another track draws no bar rather than a wrong one.
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
            lambda size: _contrasty(_album_art(self.art, size)),
            image_width,
            image_height,
            self.labels,
            self._elapsed(),
        )

    def _elapsed(self) -> timedelta | None:
        """How far into the playing track the player is at `now`; None if there's no position to draw."""
        track = self.queue.currently_playing
        if self.playback is None or track is None:
            return None
        return self.playback.elapsed(track, self.now or moment.now())


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
            cover.thumbnail((height, height))
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
