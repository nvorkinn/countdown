"""The Spotify area's client: it rotates between what's playing and the listener's top tracks and artists.

It shows Spotify, Apple Music, or both: Spotify while it's playing, else what was last played on Apple Music, with
their top lists taking turns.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient, json_content
from countdown_core.core.style import normalise, panel_style
from countdown_core.i18n import N_
from countdown_core.spotify.models import Artist, Queue, TopResponse, Track
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_styles import APPLE_MUSIC_LABELS, SPOTIFY_LABELS
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel

if TYPE_CHECKING:
    from countdown_api.models import ConfigResponse
    from countdown_core.core.panel import Panel
    from countdown_core.core.style import Style
    from countdown_core.spotify.spotify_styles import Labels
    from countdown_credentials.registration import Registration


@dataclass(frozen=True)
class MusicService:
    """One of the services the Spotify area can show: what it's called, and where the broker serves it."""

    name: str  # English, marked with N_()
    # Where the broker serves its data, in Spotify's shapes.
    api_path: str
    labels: Labels


SPOTIFY = MusicService(N_("Spotify"), "/api/spotify", SPOTIFY_LABELS)
# Apple Music has no now playing, queue or top lists, so the broker hands over the last played track and the ones
# before it, and top lists it counts from the plays it has seen since the listener connected.
APPLE_MUSIC = MusicService(N_("Apple Music"), "/api/apple-music", APPLE_MUSIC_LABELS)


@dataclass(frozen=True)
class MusicSettings:
    """What the Spotify area is built with: which services the config switches on, and the style to draw it in."""

    spotify: bool
    apple_music: bool = False
    style: str = "hero"

    @classmethod
    def from_config(cls, config: ConfigResponse) -> MusicSettings:
        """Take the switches from the broker's config; one that predates Apple Music leaves it off."""
        apple_music = config.apple_music
        return cls(
            spotify=config.spotify.enabled,
            apple_music=apple_music is not None and apple_music.enabled,
            style=panel_style(config, config.spotify.style),
        )

    @property
    def enabled(self) -> bool:
        """Whether either service is on, and so the area shown."""
        return self.spotify or self.apple_music

    @property
    def services(self) -> tuple[MusicService, ...]:
        """The services switched on, Spotify (the live one) first."""
        return tuple(service for service, on in ((SPOTIFY, self.spotify), (APPLE_MUSIC, self.apple_music)) if on)


@dataclass(frozen=True)
class Page:
    """One of the pages the Spotify area rotates through: the player, or one service's top lists over a period.

    A page with nothing to show gives way to the next one.
    """

    # None for the player, which asks every service.
    service: MusicService | None = None
    # Spotify's time range for the top lists, and the period it shows as.
    time_range: str = ""
    period: str = ""


PERIODS = (("long_term", N_("12 months")), ("short_term", N_("4 weeks")))


def pages(services: tuple[MusicService, ...]) -> tuple[Page, ...]:
    """Return the pages in turn: the player before each top page, each period's top lists one service after another."""
    return tuple(
        page
        for time_range, period in PERIODS
        for service in services
        for page in (Page(), Page(service, time_range, period))
    )


class SpotifyClient(AbstractClient):
    """Now playing and top tracks/artists, from Spotify, Apple Music or both, via auth-broker.

    The broker calls the services server-side, so this device never holds a token. Authenticates as the device
    `registration` registered.
    """

    panel_title = N_("Spotify")
    panel_logo = "spotify_logo.png"

    def __init__(self, registration: Registration, config: MusicSettings) -> None:
        """`registration`: this device at the broker, which it calls as; `config`: the services and style."""
        super().__init__(registration, config)
        self.page = -1
        self.pages = pages(config.services)
        self.base_url = registration.broker_url
        self.session.auth = registration.auth

    def _initialise(self) -> None:
        pass

    @property
    def style(self) -> Style:
        """The configured style, its old names (classic) mapped onto the shared ones."""
        return normalise(self.config.style)

    def _request(self, method: str, path: str, params: dict[str, str | int] | None = None) -> bytes:
        response = self.session.request(method, f"{self.base_url}{path}", params=params, timeout=DEFAULT_TIMEOUT)
        return json_content(response)

    def _update(self) -> Panel:
        """Show the next page, or the one after if that has nothing to show."""
        for _ in range(2):
            self.page = (self.page + 1) % len(self.pages)
            page = self.pages[self.page]
            panel = self.update_player() if page.service is None else self.update_top(page)
            if panel:
                return panel
        return self.message_panel(N_("Nothing playing on:"))

    def update_player(self) -> Panel | None:
        """What's playing on Spotify, else what was last played on Apple Music; None if neither has anything.

        Each with what's next (or, for Apple Music, what was played before).
        """
        for service in self.config.services:
            queue = Queue.model_validate_json(self._request("GET", f"{service.api_path}/queue"))
            if queue.currently_playing is not None:
                return SpotifyPanel(queue, self.style, service.labels)
        return None

    def update_top(self, page: Page) -> Panel | None:
        """Fetch the page's service's top five tracks and artists over its period; None if both are empty.

        Apple Music's are, until the broker has seen some plays. With both services on, the page names its own.
        """
        params = {"time_range": page.time_range, "limit": 5}
        path = page.service.api_path
        tracks = TopResponse[Track].model_validate_json(self._request("GET", f"{path}/top/tracks", params=params))
        artists = TopResponse[Artist].model_validate_json(self._request("GET", f"{path}/top/artists", params=params))
        if not tracks.items and not artists.items:
            return None
        service = page.service.name if len(self.config.services) > 1 else None
        return SpotifyTopPanel(page.period, tracks.items, artists.items, self.style, service)


class AppleMusicClient(SpotifyClient):
    """The Spotify area with only Apple Music on: the same, but its messages name Apple Music, not Spotify."""

    panel_title = N_("Apple Music")
    panel_logo = None


def music_client_class(config: MusicSettings) -> type[SpotifyClient]:
    """Return the client for the services the Spotify area is configured to show."""
    return AppleMusicClient if config.services == (APPLE_MUSIC,) else SpotifyClient
