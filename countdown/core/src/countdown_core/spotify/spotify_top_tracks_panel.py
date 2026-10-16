from typing import override

from countdown_core.i18n import N_
from countdown_core.spotify.models import TopResponse, Track
from countdown_core.spotify.spotify_abstract_top_panel import AbstractSpotifyTopSubPanel


class SpotifyTopTracksPanel(AbstractSpotifyTopSubPanel):
    def __init__(self, top_response: TopResponse[Track]) -> None:
        super().__init__(N_("Top tracks:"), top_response)

    @override
    def format_line(self, idx: int, row: Track) -> str:
        return f"{idx + 1}) {row.name} by {row.artists[0].name}"
