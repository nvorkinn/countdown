from collections.abc import Callable
from dataclasses import dataclass

from countdown_api.models import Spotify
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.panel import Panel
from countdown_core.spotify.models import Artist, Queue, TopResponse, Track
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_top_artists_panel import SpotifyTopArtistsPanel
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.spotify.spotify_top_tracks_panel import SpotifyTopTracksPanel
from countdown_credentials.registration import Registration


@dataclass(frozen=True)
class PageConfig:
    """One of the pages the Spotify area rotates through. A page with nothing to show
    (update_function returns None) gives way to the next one."""

    update_function: Callable[[SpotifyClient, PageConfig], Panel | None]


@dataclass(frozen=True)
class TopPageConfig(PageConfig):
    update_function: Callable[[SpotifyClient, TopPageConfig], Panel | None]
    params: dict[str, str]
    period: str


class SpotifyClient(AbstractClient):
    """Now playing and top tracks/artists, via auth-broker, which calls Spotify server-side
    so this device never holds a token. Authenticates as the device `registration` registered."""

    panel_title = "Spotify"
    panel_logo = "spotify_logo.png"

    def __init__(self, registration: Registration, config: Spotify):
        super().__init__(registration, config)
        self.page = -1
        self.base_url = registration.broker_url
        self.session.auth = registration.auth

    def _initialise(self) -> None:
        pass

    def _request(self, method: str, path: str, **kwargs) -> bytes:
        response = self.session.request(method, f"{self.base_url}{path}", timeout=DEFAULT_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response.content

    def _update(self) -> Panel:
        """The next page in PAGES, or the one after if that has nothing to show."""
        for _ in range(2):
            self.page = (self.page + 1) % len(PAGES)
            page_config = PAGES[self.page]
            panel = page_config.update_function(self, page_config)
            if panel:
                return panel
        return self.message_panel("Nothing playing on:")

    def update_player(self, _page_config: PageConfig) -> Panel | None:
        """What's playing and what's up next, or None if nothing is playing."""
        queue = Queue.model_validate_json(self._request("GET", "/api/spotify/queue"))
        if queue.currently_playing is None:
            return None
        return SpotifyPanel(queue)

    def update_top(self, page_config: TopPageConfig) -> Panel:
        params = {**page_config.params, "limit": 5}
        top_tracks_bytes = self._request("GET", "/api/spotify/top/tracks", params=params)
        top_tracks_panel = SpotifyTopTracksPanel(TopResponse[Track].model_validate_json(top_tracks_bytes))
        top_artists_bytes = self._request("GET", "/api/spotify/top/artists", params=params)
        top_artists_panel = SpotifyTopArtistsPanel(TopResponse[Artist].model_validate_json(top_artists_bytes))
        return SpotifyTopPanel(page_config.period, top_tracks_panel, top_artists_panel)


PAGES: tuple[PageConfig, ...] = (
    PageConfig(SpotifyClient.update_player),
    TopPageConfig(SpotifyClient.update_top, {"time_range": "long_term"}, "12 months"),
    PageConfig(SpotifyClient.update_player),
    TopPageConfig(SpotifyClient.update_top, {"time_range": "short_term"}, "4 weeks"),
)
