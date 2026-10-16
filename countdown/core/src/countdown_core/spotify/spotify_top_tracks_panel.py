"""The top tracks half of the classic top page."""

from typing import TYPE_CHECKING, override

from countdown_core.i18n import N_
from countdown_core.spotify.spotify_abstract_top_panel import AbstractSpotifyTopSubPanel

if TYPE_CHECKING:
    from countdown_core.spotify.models import TopResponse, Track


class SpotifyTopTracksPanel(AbstractSpotifyTopSubPanel):
    """The listener's top tracks, each with its first artist."""

    def __init__(self, top_response: TopResponse[Track]) -> None:
        """`top_response`: the tracks, from Spotify."""
        super().__init__(N_("Top tracks:"), top_response)

    @override
    def format_line(self, idx: int, row: Track) -> str:
        return f"{idx + 1}) {row.name} by {row.artists[0].name}"
