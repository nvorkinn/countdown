"""The Spotify area's client: it rotates between what's playing and the listener's top tracks and artists."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.i18n import N_
from countdown_core.spotify.models import Artist, Queue, TopResponse, Track
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel

if TYPE_CHECKING:
    from collections.abc import Callable

    from countdown_api.models import Spotify
    from countdown_core.core.panel import Panel
    from countdown_core.spotify.spotify_panel import Style
    from countdown_credentials.registration import Registration


@dataclass(frozen=True)
class PageConfig:
    """One of the pages the Spotify area rotates through.

    A page with nothing to show (update_function returns None) gives way to the next one.
    """

    update_function: Callable[[SpotifyClient, PageConfig], Panel | None]


@dataclass(frozen=True)
class TopPageConfig(PageConfig):
    """A top tracks and artists page: `params` for Spotify's time range, and the `period` it shows as."""

    update_function: Callable[[SpotifyClient, TopPageConfig], Panel | None]
    params: dict[str, str]
    period: str


class SpotifyClient(AbstractClient):
    """Now playing and top tracks/artists, via auth-broker, which calls Spotify server-side.

    So this device never holds a token. Authenticates as the device `registration` registered.
    """

    panel_title = N_("Spotify")
    panel_logo = "spotify_logo.png"

    def __init__(self, registration: Registration, config: Spotify) -> None:
        """`registration`: this device at the broker, which it calls as; `config`: the Spotify settings."""
        super().__init__(registration, config)
        self.page = -1
        self.base_url = registration.broker_url
        self.session.auth = registration.auth

    def _initialise(self) -> None:
        pass

    @property
    def style(self) -> Style:
        """The configured style; the retired classic, which a broker that hasn't migrated may still send, is hero."""
        style = self.config.style
        return "hero" if style == "classic" else style

    def _request(self, method: str, path: str, params: dict[str, str | int] | None = None) -> bytes:
        response = self.session.request(method, f"{self.base_url}{path}", params=params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.content

    def _update(self) -> Panel:
        """Show the next page in PAGES, or the one after if that has nothing to show."""
        for _ in range(2):
            self.page = (self.page + 1) % len(PAGES)
            page_config = PAGES[self.page]
            panel = page_config.update_function(self, page_config)
            if panel:
                return panel
        return self.message_panel(N_("Nothing playing on:"))

    def update_player(self, _page_config: PageConfig) -> Panel | None:
        """What's playing and what's up next, or None if nothing is playing."""
        queue = Queue.model_validate_json(self._request("GET", "/api/spotify/queue"))
        if queue.currently_playing is None:
            return None
        return SpotifyPanel(queue, self.style)

    def update_top(self, page_config: TopPageConfig) -> Panel:
        """Fetch the listener's top five tracks and artists over the page's period."""
        params = {**page_config.params, "limit": 5}
        tracks = TopResponse[Track].model_validate_json(self._request("GET", "/api/spotify/top/tracks", params=params))
        artists_bytes = self._request("GET", "/api/spotify/top/artists", params=params)
        artists = TopResponse[Artist].model_validate_json(artists_bytes)
        return SpotifyTopPanel(page_config.period, tracks.items, artists.items, self.style)


PAGES: tuple[PageConfig, ...] = (
    PageConfig(SpotifyClient.update_player),
    TopPageConfig(SpotifyClient.update_top, {"time_range": "long_term"}, N_("12 months")),
    PageConfig(SpotifyClient.update_player),
    TopPageConfig(SpotifyClient.update_top, {"time_range": "short_term"}, N_("4 weeks")),
)
