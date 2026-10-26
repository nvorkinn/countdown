"""The Spotify area's top page: the listener's top tracks and artists over a period."""

from typing import TYPE_CHECKING, override

from countdown_renderer.panel import Panel
from countdown_renderer.spotify.spotify_styles import STYLES

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.spotify.models import Artist, Track
    from countdown_renderer.style import Style
    from countdown_renderer.utils.clock import ClockFormat


class SpotifyTopPanel(Panel):
    """The top tracks and artists side by side, under a heading naming the period."""

    scales = True

    def __init__(
        self, period: str, tracks: list[Track], artists: list[Artist], style: Style = "hero", service: str | None = None
    ) -> None:
        """`period`: what the lists cover, in English ("4 weeks"); the two lists; `style`: how to draw them.

        `service` names whose lists they are (English), when the area shows more than one.
        """
        super().__init__()
        self.period = period
        self.service = service
        self.tracks = tracks
        self.artists = artists
        self.style = style

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        return STYLES[self.style].top(self.period, self.tracks, self.artists, image_width, image_height, self.service)
