from typing import TYPE_CHECKING, override

from countdown_core.i18n import N_
from countdown_core.spotify.spotify_abstract_top_panel import AbstractSpotifyTopSubPanel

if TYPE_CHECKING:
    from countdown_core.spotify.models import Artist, TopResponse


class SpotifyTopArtistsPanel(AbstractSpotifyTopSubPanel):
    def __init__(self, top_response: TopResponse[Artist]) -> None:
        super().__init__(N_("Top artists:"), top_response)

    @override
    def format_line(self, idx: int, row: Artist) -> str:
        return f"{idx + 1}) {row.name}"
