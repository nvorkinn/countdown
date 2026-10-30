"""The API clients a render draws with, built from the broker's config, and the panels they give."""

import logging
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from countdown_api.models import ConfigResponse, Glowmarkt
from countdown_renderer.abstract_client import AbstractClient, ClientStatus
from countdown_renderer.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_renderer.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_renderer.glow.glow_client import GlowClient
from countdown_renderer.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_renderer.spotify.spotify_client import MusicSettings, SpotifyClient, music_client_class
from countdown_renderer.style import panel_style
from countdown_renderer.weather.weather_client import WeatherClient
from countdown_shared.i18n import N_

if TYPE_CHECKING:
    from collections.abc import Mapping

    from countdown_renderer.panel import Panel

logger = logging.getLogger(__name__)


class ClientClasses(Enum):
    """Every API the screen can show: its name, and the client class that polls it."""

    NOTICE_BOARD = ("notice_board", NoticeBoardClient)
    GLOWMARKT = ("glowmarkt", GlowClient)
    # Departures from every source; the name is that of the broker's `tfl` section, which the layouts go by.
    TFL = ("tfl", DeparturesClient)
    WEATHER = ("weather", WeatherClient)
    # Spotify, Apple Music or both; AppleMusicClient when it's only Apple Music (see client_class).
    SPOTIFY = ("spotify", SpotifyClient)
    # Drawn above Spotify, or in its place (see rendering.place_developer_panel).
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
            parcels=config.parcels is not None and config.parcels.enabled,
        )
    if name == ClientClasses.SPOTIFY.api_name:
        return MusicSettings.from_config(config)
    if name == ClientClasses.DEVELOPER.api_name:
        # The section is optional, and the panel has no style of its own to fall back on.
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

    def __init__(self, device_id: str, config: object, error: Exception) -> None:
        """Stand in for the client that was built for `device_id` with `config` and raised `error`."""
        super().__init__(device_id, config)
        self.error = error
        self.status = ClientStatus.ERROR

    def _initialise(self) -> None:
        raise self.error

    def _update(self) -> Panel:
        raise self.error


class ApiRegistry:
    """The API clients one render draws with, built afresh from the config for that render alone.

    Nothing of a registry outlives its render (see render.render): the clients, what they read and their statuses are
    the render's, and the next render builds its own.
    """

    def __init__(self, device_id: str, config: ConfigResponse) -> None:
        """Build a client for every API `config` switches on, each with its section of the config.

        `device_id` is handed to every client: the device whose keys they read. A client whose constructor raises is
        logged, and stands in as a FailedClient, so one broken API doesn't take the others down. The language and time
        zone must be set first (i18n.set_locale): some clients put their texts together as they read.
        """
        self.device_id = device_id
        self.clients: dict[str, AbstractClient] = {}
        for member in ClientClasses:
            name = member.api_name
            section = section_for(name, config)
            if not is_enabled(section):
                continue
            try:
                self.clients[name] = client_class(member, section)(device_id, section)
            except Exception as e:
                logger.exception("Error building %s client", name)
                self.clients[name] = FailedClient(device_id, section, e)
        self._share_notice_sources()

    def _share_notice_sources(self) -> None:
        """Hand the notice board the other clients' notice sources."""
        board = self.clients.get(ClientClasses.NOTICE_BOARD.api_name)
        if isinstance(board, NoticeBoardClient):
            board.use_client_sources(
                source for client in self.clients.values() if client is not board for source in client.notice_sources()
            )

    def poll(self, only: ClientClasses | None = None) -> dict[str, Panel | None]:
        """Read every client's data (or `only` that API's), one after another, and return the panel each gave, by API.

        A client that can't ask Redis (a RedisError) raises it, unlogged, and the rest aren't read: the render then
        fails and writes nothing, so the screen keeps the frame it has. Any other failure (a bug) is logged, and that
        client gives no panel: its area says it could not connect (see panels()). A key that has expired is no failure:
        the client gives a "Not available" panel (AbstractClient.poll).
        """
        polled: dict[str, Panel | None] = {}
        for name, client in self.clients.items():
            if only is not None and name != only.api_name:
                continue
            try:
                polled[name] = client.poll()
            except RedisError:
                raise
            except Exception:
                if not isinstance(client, FailedClient):  # whose constructor's error was logged as it was built
                    logger.exception("Error updating %s", name)
        return polled

    def panels(self, polled: Mapping[str, Panel | None]) -> dict[str, Panel]:
        """Return a panel for every API: the one it gave (`polled`, from poll()), or a MessagePanel saying why not."""
        return {member.api_name: self._panel_for(member, polled) for member in ClientClasses}

    def statuses(self) -> dict[str, ClientStatus]:
        """Return where each client stands, by API, for the metrics; an API with no client isn't there (DISABLED)."""
        return {name: client.status for name, client in self.clients.items()}

    def expired(self) -> frozenset[str]:
        """Return the APIs whose panel says "Not available" because the store had nothing for them."""
        return frozenset(name for name, client in self.clients.items() if client.expired)

    def available(self) -> frozenset[ClientClasses]:
        """Return the APIs that get an area on the screen.

        A failing client still counts, so its area says "Could not connect" rather than vanishing.
        """
        available = frozenset(
            member
            for member in ClientClasses
            if (client := self.clients.get(member.api_name)) is not None and not client.is_disabled()
        )
        if ClientClasses.DEVELOPER not in available and self._music_is_idle():
            # Nothing playing: the music's room goes to the stops and the notices, as if it weren't set up. (With the
            # developer panel on, that takes the music's place instead: see rendering.place_developer_panel.)
            available -= {ClientClasses.SPOTIFY}
        return available

    def _music_is_idle(self) -> bool:
        """Whether the music area is folded away now (SpotifyClient.is_idle); False without a working music client."""
        music = self.clients.get(ClientClasses.SPOTIFY.api_name)
        return isinstance(music, SpotifyClient) and not music.is_disabled() and music.is_idle()

    def show_stops(self, count: int) -> None:
        """Tell the departures client how many stops the layout has room for. Call before poll()."""
        departures = self.clients.get(ClientClasses.TFL.api_name)
        if isinstance(departures, DeparturesClient):
            departures.stops_per_update = count

    def _panel_for(self, member: ClientClasses, polled: Mapping[str, Panel | None]) -> Panel:
        """Return the API's panel, or a MessagePanel saying why there isn't one."""
        name, clazz = member.value
        client = self.clients.get(name)
        if client is None or client.is_disabled():
            return clazz.message_panel(N_("Not configured"))
        # The client's own class where it has one, which says whose service it is (Spotify's or Apple Music's).
        named = type(client) if isinstance(client, clazz) else clazz
        return polled.get(name) or named.message_panel(N_("Could not connect"))
