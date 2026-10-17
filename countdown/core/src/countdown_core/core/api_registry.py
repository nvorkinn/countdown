"""The API clients, built and rebuilt from the broker's config, and the panels they last returned."""

import asyncio
import logging
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfoNotFoundError

from redis.exceptions import RedisError

from countdown_api.models import ConfigResponse, Glowmarkt
from countdown_core.core.abstract_client import AbstractClient, ClientStatus
from countdown_core.core.style import panel_style
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_core.glow.glow_client import GlowClient
from countdown_core.i18n import N_, set_locale
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.spotify.spotify_client import MusicSettings, SpotifyClient, music_client_class
from countdown_core.store import reader
from countdown_core.weather.weather_client import WeatherClient

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
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

    The departures' is its `departures` section (or, from an older broker, `tfl`). The notice board's also carries
    the TfL app key, for the road incidents near the postcode. Every panel that has a style gets the config's shared
    one (see panel_style).
    """
    if name == ClientClasses.TFL.api_name:
        return DeparturesSettings.from_config(config)
    if name == ClientClasses.NOTICE_BOARD.api_name:
        return NoticeBoardSettings(
            postcode=config.notice_board.postcode,
            country=config.notice_board.country,
            style=panel_style(config, config.notice_board.style),
            tfl_app_key=config.tfl.app_key,
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

    Only the music (Spotify, Apple Music) and the developer panel have switches; Glowmarkt is on when both
    credentials are there (a cleared
    field can arrive as "" or whitespace as well as null); the rest are always on.
    """
    match section:
        case MusicSettings() | DeveloperSettings():
            return section.enabled
        case Glowmarkt():
            return all((value or "").strip() for value in (section.username, section.password))
        case _:
            return True


class FailedClient(AbstractClient):
    """Stands in for a client whose constructor raised, so the failure shows as an error status.

    It shows in HA too, instead of the client silently being missing. Rebuilt when its config changes.
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
    """The API clients the broker's config asks for, and the panel each last returned."""

    def __init__(self, registration: Registration, publisher: OtlpPublisher) -> None:
        """`registration` is handed to every client; `publisher` reports their statuses from now on."""
        self.registration = registration  # handed to every client
        self.clients: dict[str, AbstractClient] = {}
        self.panels: dict[str, Panel | None] = {}  # None only from a DISABLED client
        # The publisher has been reporting since boot; from now on it reports these clients too.
        # Shares self.clients (mutated in place, never reassigned) to read their statuses.
        self.pub = publisher
        self.pub.clients = self.clients
        self.status = publisher.device_status
        # The language and time zone the clients were built for; a change rebuilds them all (see on_config_update).
        self.locale: tuple[str, str] | None = None

    async def on_config_update(self, config: ConfigResponse) -> None:
        """Bring the clients in line with `config`, rebuilding only those whose needs_refresh() says so.

        Failures are logged, not raised, so one broken API doesn't take the others down. A new language or time zone
        rebuilds every client, as some put their texts together as they fetch (the notices).
        """
        self._apply_locale(config)
        to_initialise: dict[str, AbstractClient] = {}
        for member in ClientClasses:
            name = member.api_name
            client_config = section_for(name, config)
            clazz = client_class(member, client_config)
            current = self.clients.get(name)

            if not is_enabled(client_config):
                # No client at all, so one switched back on later is built fresh.
                self._drop(name)
            elif current is None or current.needs_refresh(client_config):
                # The old panel came from the old config, so it goes too.
                self._drop(name)
                try:
                    to_initialise[name] = self.clients[name] = clazz(self.registration, client_config)
                except Exception as e:
                    logger.exception("Error building %s client", name)
                    self.clients[name] = FailedClient(self.registration, client_config, e)

        self._share_notice_sources()
        outcomes = await asyncio.gather(*[c.initialise() for c in to_initialise.values()], return_exceptions=True)
        for name, outcome in zip(to_initialise, outcomes, strict=True):
            if isinstance(outcome, Exception):
                logger.error("Error initialising %s: %s", name, outcome, exc_info=outcome)

    def _share_notice_sources(self) -> None:
        """Hand the notice board the other clients' notice sources, which a rebuild of any of them replaces."""
        board = self.clients.get(ClientClasses.NOTICE_BOARD.api_name)
        if isinstance(board, NoticeBoardClient):
            board.use_client_sources(
                source for client in self.clients.values() if client is not board for source in client.notice_sources()
            )

    def _apply_locale(self, config: ConfigResponse) -> None:
        """Switch to the config's language and time zone, dropping every client if they changed."""
        locale = (config.language, config.timezone)
        if locale == self.locale:
            return
        try:
            set_locale(*locale)
        except ZoneInfoNotFoundError, ValueError:
            logger.warning("Unknown time zone %r; using Europe/London", config.timezone)
            set_locale(config.language, "Europe/London")
        if self.locale is not None:
            for name in list(self.clients):
                self._drop(name)
        self.locale = locale

    def _drop(self, name: str) -> None:
        self.clients.pop(name, None)
        self.panels.pop(name, None)

    async def update_all(self) -> dict[str, Panel]:
        """Poll the clients that are due and return a panel for every API.

        A client that isn't due, or that raised, keeps its last panel.
        """
        due = [(api_name, client.update()) for api_name, client in self.clients.items() if client.is_due]
        outcomes = await asyncio.gather(*[coro for _, coro in due], return_exceptions=True)

        for (api_name, _), outcome in zip(due, outcomes, strict=True):
            if isinstance(outcome, Exception):
                logger.error("Error updating %s: %s", api_name, outcome, exc_info=outcome)
            else:
                self.panels[api_name] = outcome
        return {member.api_name: self._panel_for(member) for member in ClientClasses}

    async def pace(self) -> int | None:
        """Return the shortest refresh pace, in seconds, that any provider asks for now; None if none does.

        Read from the store (the providers write it there, with a short TTL), so None as well when this renderer reads
        no store, or can't reach it: the screen then keeps its usual pace.
        """
        store = reader.store()
        pace_keys = [key for client in self.clients.values() for key in client.pace_keys()]
        if store is None or not pace_keys:
            return None
        try:
            return await asyncio.to_thread(reader.fastest_pace, store, pace_keys)
        except RedisError as e:
            logger.warning("Couldn't read the refresh pace from the store (%s); keeping the usual", e)
            return None

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
        """Tell the departures client how many stops the layout has room for.

        Call after every on_config_update(), since that may have rebuilt the client.
        """
        departures = self.clients.get(ClientClasses.TFL.api_name)
        if isinstance(departures, DeparturesClient):
            departures.stops_per_update = count

    def _panel_for(self, member: ClientClasses) -> Panel:
        """Return the API's last panel, or a MessagePanel saying why there isn't one."""
        name, clazz = member.value
        client = self.clients.get(name)
        if client is None or client.is_disabled():
            return clazz.message_panel(N_("Not configured"))
        # The client's own class where it has one, which says whose service it is (Spotify's or Apple Music's).
        named = type(client) if isinstance(client, clazz) else clazz
        return self.panels.get(name) or named.message_panel(N_("Could not connect"))
