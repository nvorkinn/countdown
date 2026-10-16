from math import floor

from PIL import Image, ImageDraw

from countdown_core.core.panel import Panel
from countdown_core.spotify.spotify_panel import Style
from countdown_core.spotify.spotify_styles import STYLES
from countdown_core.spotify.spotify_top_artists_panel import SpotifyTopArtistsPanel
from countdown_core.spotify.spotify_top_tracks_panel import SpotifyTopTracksPanel
from countdown_core.utils.utils import UBUNTU_MEDIUM_15


class SpotifyTopPanel(Panel):
    def __init__(
        self,
        period: str,
        top_tracks_panel: SpotifyTopTracksPanel,
        top_artists_panel: SpotifyTopArtistsPanel,
        style: Style = "classic",
    ) -> None:
        super().__init__()
        self.period = period
        self.sub_panels = [top_tracks_panel, top_artists_panel]
        self.style = style

    def render(self, image_width: int, image_height: int) -> Image.Image:
        if self.style != "classic":
            tracks, artists = (panel.top_response.items for panel in self.sub_panels)
            return STYLES[self.style].top(self.period, tracks, artists, image_width, image_height)
        img = Image.new("RGBA", (image_width, image_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.font = UBUNTU_MEDIUM_15
        draw.fontmode = "1"

        draw.text((image_width / 2, 0), f"Your Spotify over the last {self.period}:", "black", anchor="ma")
        x = 0
        for sub_panel in self.sub_panels:
            panel = sub_panel.render(floor(image_width / 2), image_height)
            img.paste(panel, (x, 20), panel)
            x += panel.size[0] + 10

        return img
