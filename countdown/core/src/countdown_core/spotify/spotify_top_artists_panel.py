from typing import override

from countdown_core.i18n import N_
from countdown_core.spotify.models import Artist, TopResponse
from countdown_core.spotify.spotify_abstract_top_panel import AbstractSpotifyTopSubPanel


class SpotifyTopArtistsPanel(AbstractSpotifyTopSubPanel):
    def __init__(self, top_response: TopResponse[Artist]) -> None:
        super().__init__(N_("Top artists:"), top_response)

    @override
    def format_line(self, idx: int, row: Artist) -> str:
        return f"{idx + 1}) {row.name}"
