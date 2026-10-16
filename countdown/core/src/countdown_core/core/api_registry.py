"""The API clients, built and rebuilt from the broker's config, and the panels they last returned."""

import asyncio
import logging
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfoNotFoundError

from countdown_api.models import ConfigResponse, Glowmarkt, Spotify
from countdown_core.core.abstract_client import AbstractClient, ClientStatus
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.glow.glow_client import GlowClient
from countdown_core.i18n import N_, set_locale
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.spotify.spotify_client import SpotifyClient
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
    SPOTIFY = ("spotify", SpotifyClient)

    @property
    def api_name(self) -> str:
        """The API's name, as the broker's config and the layouts know it."""
        return self.value[0]


API_NAMES = [member.api_name for member in ClientClasses]


def section_for(name: str, config: ConfigResponse) -> object:
    """Return what the API `name` is configured with: its section of the broker's config.

    The departures' is its `departures` section (or, from an older broker, `tfl`). The notice board's also carries
    the departures, as its disruptions are about their stops (so the broker needn't send them twice).
    """
    if name == ClientClasses.TFL.api_name:
        return DeparturesSettings.from_config(config)
    if name == ClientClasses.NOTICE_BOARD.api_name:
        return NoticeBoardSettings(
            postcode=config.notice_board.postcode,
            country=config.notice_board.country,
            style=config.notice_board.style,
            departures=DeparturesSettings.from_config(config),
        )
    return getattr(config, name)


def is_enabled(section: object) -> bool:
    """Whether the broker's config switches the API on.

    Only Spotify has a switch; Glowmarkt is on when both credentials are there (a cleared field can arrive as "" or
    whitespace as well as null); the rest are always on.
    """
    match section:
        case Spotify():
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
            name, clazz = member.value
            client_config = section_for(name, config)
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

        outcomes = await asyncio.gather(*[c.initialise() for c in to_initialise.values()], return_exceptions=True)
        for name, outcome in zip(to_initialise, outcomes, strict=True):
            if isinstance(outcome, Exception):
                logger.error("Error initialising %s: %s", name, outcome, exc_info=outcome)

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
        return self.panels.get(name) or clazz.message_panel(N_("Could not connect"))
