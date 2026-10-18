"""The API clients a render draws with, built from the broker's config, and the panels they give."""

import logging
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from countdown_api.models import ConfigResponse, Glowmarkt
from countdown_core.core.abstract_client import AbstractClient, ClientStatus
from countdown_core.core.style import panel_style
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_core.glow.glow_client import GlowClient
from countdown_core.i18n import N_
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.spotify.spotify_client import MusicSettings, SpotifyClient, music_client_class
from countdown_core.weather.weather_client import WeatherClient

if TYPE_CHECKING:
    from collections.abc import Mapping

    from countdown_core.core.panel import Panel
    from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)


class ClientClasses(Enum):
    """Every API the screen can show: its name, and the client class that polls it."""

    NOTICE_BOARD = ("notice_board", NoticeBoardClient)
    GLOWMARKT = ("glowmarkt", GlowClient)
    # Departures from every source; named for the broker's first section, which the layouts still go by.
    TFL = ("tfl", DeparturesClient)
    WEATHER = ("weather", WeatherClient)
    # Spotify, Apple Music or both; AppleMusicClient when it's only Apple Music (see client_class).
    SPOTIFY = ("spotify", SpotifyClient)
    # Shares Spotify's area (see rendering.share_spotify_area).
    DEVELOPER = ("developer", DeveloperClient)

    @property
    def api_name(self) -> str:
        """The API's name, as the broker's config and the layouts know it."""
        return self.value[0]


API_NAMES = [member.api_name for member in ClientClasses]


def section_for(name: str, config: ConfigResponse) -> object:
    """Return what the API `name` is configured with: its section of the broker's config.

    The departures' is its `departures` section (or, from an older broker, `tfl`). Every panel that has a style gets
    the config's shared one (see panel_style).
    """
    if name == ClientClasses.TFL.api_name:
        return DeparturesSettings.from_config(config)
    if name == ClientClasses.NOTICE_BOARD.api_name:
        return NoticeBoardSettings(
            postcode=config.notice_board.postcode,
            country=config.notice_board.country,
            style=panel_style(config, config.notice_board.style),
        )
    if name == ClientClasses.SPOTIFY.api_name:
        return MusicSettings.from_config(config)
    if name == ClientClasses.DEVELOPER.api_name:
        # A broker that predates the developer panel leaves it off; it has no style of its own to fall back on.
        enabled = config.developer is not None and config.developer.enabled
        return DeveloperSettings(enabled=enabled, style=panel_style(config, "hero"))
    return getattr(config, name)


def client_class(member: ClientClasses, section: object) -> type[AbstractClient]:
    """Return the class that polls the API `member`, configured with `section`: the Spotify area's depends on it."""
    if isinstance(section, MusicSettings):
        return music_client_class(section)
    return member.value[1]


def is_enabled(section: object) -> bool:
    """Whether the broker's config switches the API on.

    The music (Spotify, Apple Music), the developer panel and Glowmarkt have switches; the rest are always on.
    """
    match section:
        case MusicSettings() | DeveloperSettings() | Glowmarkt():
            return section.enabled
        case _:
            return True


class FailedClient(AbstractClient):
    """Stands in for a client whose constructor raised, so the failure shows as an error status.

    It shows in the metrics too, instead of the client silently being missing.
    """

    poll_interval = timedelta(minutes=10)

    def __init__(self, registration: Registration, config: object, error: Exception) -> None:
        """Stand in for the client that was built with `registration` and `config` and raised `error`."""
        super().__init__(registration, config)
        self.error = error
        self.status = ClientStatus.ERROR

    def _initialise(self) -> None:
        raise self.error

    def _update(self) -> Panel:
        raise self.error


class ApiRegistry:
    """The API clients one render draws with, built afresh from the config for that render alone.

    Nothing of a registry outlives its render (see render.render): the clients, what they read and their statuses are
    the render's, and the next render builds its own. What a screen last showed comes in from outside, as the previous
    render's panels (poll's `previous`), never from a registry kept between them.
    """

    def __init__(self, registration: Registration, config: ConfigResponse) -> None:
        """Build a client for every API `config` switches on, each with its section of the config.

        `registration` is handed to every client: the device whose keys they read. A client whose constructor raises
        is logged, and stands in as a FailedClient, so one broken API doesn't take the others down. The language and
        time zone must be set first (i18n.set_locale): some clients put their texts together as they read.
        """
        self.registration = registration
        self.clients: dict[str, AbstractClient] = {}
        # What each client was built with, so the next render can tell whether this one's panel is still its own.
        self.sections: dict[str, object] = {}
        for member in ClientClasses:
            name = member.api_name
            section = section_for(name, config)
            if not is_enabled(section):
                continue
            self.sections[name] = section
            try:
                self.clients[name] = client_class(member, section)(registration, section)
            except Exception as e:
                logger.exception("Error building %s client", name)
                self.clients[name] = FailedClient(registration, section, e)
        self._share_notice_sources()

    def _share_notice_sources(self) -> None:
        """Hand the notice board the other clients' notice sources."""
        board = self.clients.get(ClientClasses.NOTICE_BOARD.api_name)
        if isinstance(board, NoticeBoardClient):
            board.use_client_sources(
                source for client in self.clients.values() if client is not board for source in client.notice_sources()
            )

    def poll(self, previous: Mapping[str, Panel] | None = None) -> dict[str, Panel]:
        """Read every client's data, one after another, and return the panel each gave, by API.

        A client that raises (Redis not answering, a bug) gives its panel in `previous` if there's one there: the panel
        the screen last had from it, which the caller has checked is still this config's. Otherwise it gives none, and
        its area says it could not connect (see panels()). A key that has expired is no failure: the client gives a
        "Not available" panel (AbstractClient.poll).
        """
        previous = previous or {}
        polled: dict[str, Panel] = {}
        for name, client in self.clients.items():
            try:
                polled[name] = client.poll()
            except Exception:
                if not isinstance(client, FailedClient):  # whose constructor's error was logged as it was built
                    logger.exception("Error updating %s", name)
                if name in previous:
                    polled[name] = previous[name]
        return polled

    def panels(self, polled: Mapping[str, Panel]) -> dict[str, Panel]:
        """Return a panel for every API: the one it gave (`polled`, from poll()), or a MessagePanel saying why not."""
        return {member.api_name: self._panel_for(member, polled) for member in ClientClasses}

    def statuses(self) -> dict[str, ClientStatus]:
        """Return where each client stands, by API, for the metrics; an API with no client isn't there (DISABLED)."""
        return {name: client.status for name, client in self.clients.items()}

    def expired(self) -> frozenset[str]:
        """Return the APIs whose panel says "Not available" because the store had nothing for them."""
        return frozenset(name for name, client in self.clients.items() if client.expired)

    def pace_keys(self) -> list[str]:
        """Return the store keys (keys.pace) where these clients' providers can ask for a faster refresh."""
        return [key for client in self.clients.values() for key in client.pace_keys()]

    def available(self) -> frozenset[ClientClasses]:
        """Return the APIs that get an area on the screen.

        A failing client still counts, so its area says "Could not connect" rather than vanishing.
        """
        return frozenset(
            member
            for member in ClientClasses
            if (client := self.clients.get(member.api_name)) is not None and not client.is_disabled()
        )

    def show_stops(self, count: int) -> None:
        """Tell the departures client how many stops the layout has room for. Call before poll()."""
        departures = self.clients.get(ClientClasses.TFL.api_name)
        if isinstance(departures, DeparturesClient):
            departures.stops_per_update = count

    def _panel_for(self, member: ClientClasses, polled: Mapping[str, Panel]) -> Panel:
        """Return the API's panel, or a MessagePanel saying why there isn't one."""
        name, clazz = member.value
        client = self.clients.get(name)
        if client is None or client.is_disabled():
            return clazz.message_panel(N_("Not configured"))
        # The client's own class where it has one, which says whose service it is (Spotify's or Apple Music's).
        named = type(client) if isinstance(client, clazz) else clazz
        return polled.get(name) or named.message_panel(N_("Could not connect"))
