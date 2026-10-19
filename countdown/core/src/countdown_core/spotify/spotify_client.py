"""The Spotify area's client: it rotates between what's playing and the listener's top tracks and artists.

It shows Spotify, Apple Music, or both: Spotify while it's playing, else what was last played on Apple Music, with
their top lists taking turns.

They're read from the store, from what auth-broker's music provider wrote there (countdown_core.store.music) with the
broker's own tokens, so this renderer never calls the services or the broker for them. The playing track's cover too:
the provider stores the image (keys.album_art), so the renderer never calls the services' image hosts either.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from countdown_core.core import rotation
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.core.style import normalise, panel_style
from countdown_core.i18n import N_
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_styles import APPLE_MUSIC_LABELS, SPOTIFY_LABELS
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.store import keys, reader
from countdown_core.store.music import StoredPlayer, StoredTop, TopLists, art_image

if TYPE_CHECKING:
    from collections.abc import Callable

    from countdown_api.models import ConfigResponse
    from countdown_core.core.panel import Panel
    from countdown_core.core.style import Style
    from countdown_core.spotify.models import Album, Queue
    from countdown_core.spotify.spotify_styles import Labels

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MusicService:
    """One of the services the Spotify area can show: what it's called, and where its part of the store is."""

    name: str  # English, marked with N_()
    labels: Labels
    # Its part of what the broker's music provider stores (countdown_core.store.music).
    stored_as: str


SPOTIFY = MusicService(N_("Spotify"), SPOTIFY_LABELS, "spotify")
# Apple Music has no now playing, queue or top lists, so the broker hands over the last played track and the ones
# before it, and top lists it counts from the plays it has seen since the listener connected.
APPLE_MUSIC = MusicService(N_("Apple Music"), APPLE_MUSIC_LABELS, "apple_music")


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


class NoStoredMusicError(reader.NoStoredDataError):
    """The store has nothing for one of the device's services: the music provider is down, or hasn't got to it yet.

    Raised so the panel says it's not available, rather than show what was playing before the key expired.
    """


class SpotifyClient(AbstractClient):
    """Now playing and top tracks/artists, from Spotify, Apple Music or both, read from the store.

    auth-broker's music provider calls the services server-side, so this device never holds a token; it reads the
    keys of the screen `device_id`.
    """

    panel_title = N_("Spotify")
    panel_logo = "spotify_logo.png"

    def __init__(self, device_id: str, config: MusicSettings) -> None:
        """`device_id`: this screen, whose keys it reads; `config`: the services and style.

        Raises without a store.
        """
        super().__init__(device_id, config)
        self.store = reader.require_store()
        # The covers are image files, which the store's own client can't hand back (it decodes values as text).
        self.art_store = reader.binary_store()
        self.pages = pages(config.services)

    def _initialise(self) -> None:
        pass

    @property
    def style(self) -> Style:
        """The configured style, its old names (classic) mapped onto the shared ones."""
        return normalise(self.config.style)

    def _update(self) -> Panel:
        """Show this minute's page (see core.rotation), or the one after if that has nothing to show."""
        first = rotation.page(len(self.pages))
        for offset in range(2):
            page = self.pages[(first + offset) % len(self.pages)]
            panel = self.update_player() if page.service is None else self.update_top(page)
            if panel:
                return panel
        return self.message_panel(N_("Nothing playing on:"))

    def update_player(self) -> Panel | None:
        """What's playing on Spotify, else what was last played on Apple Music; None if neither has anything.

        Each with what's next (or, for Apple Music, what was played before).
        """
        stored = self._read_store(StoredPlayer, keys.spotify)
        for service in self.config.services:
            queue = _stored_queue(stored, service)
            if queue.currently_playing is not None:
                return SpotifyPanel(queue, self.style, service.labels, self._read_art(queue.currently_playing.album))
        return None

    def _read_art(self, album: Album) -> bytes | None:
        """Return the album's cover from the store, the image file's bytes; None if there's none to draw.

        None for a track with no cover (a local file), and for a cover the store hasn't got (the provider couldn't
        fetch it, or Redis evicted it) or couldn't hand over: the page is then drawn without one, not left undrawn.
        """
        image = art_image(album)
        if image is None or self.art_store is None:
            return None
        key = keys.album_art(image.url)
        try:
            art = self.art_store.get(key)
        except RedisError as e:
            logger.warning("Couldn't read the cover %s from the store (%s); drawing the player without it", key, e)
            return None
        if art is None:
            logger.info("No cover in the store under %s; drawing the player without it", key)
        return art

    def update_top(self, page: Page) -> Panel | None:
        """Read the page's service's top five tracks and artists over its period; None if both are empty.

        Apple Music's are, until the broker has seen some plays. With both services on, the page names its own.
        """
        stored = self._read_store(StoredTop, keys.spotify_top)
        lists = _stored_top(stored, page)
        tracks, artists = lists.tracks.items, lists.artists.items
        if not tracks and not artists:
            return None
        service = page.service.name if len(self.config.services) > 1 else None
        return SpotifyTopPanel(page.period, tracks, artists, self.style, service)

    def _read_store[S: (StoredPlayer, StoredTop)](self, model: type[S], key: Callable[[str], str]) -> S:
        """Read this device's `key` as `model`.

        Raises NoStoredMusicError if the key isn't there, and RedisError if Redis can't be asked.
        """
        stored = reader.read(self.store, key(self.device_id), model)
        if stored is None:
            msg = f"No music in the store under {key(self.device_id)}"
            raise NoStoredMusicError(msg)
        return stored


def _stored_queue(stored: StoredPlayer, service: MusicService) -> Queue:
    """Return `service`'s stored queue; raises NoStoredMusicError if there's none (the provider couldn't fetch it)."""
    queue = getattr(stored, service.stored_as)
    if queue is None:
        msg = f"No {service.name} queue in the store"
        raise NoStoredMusicError(msg)
    return queue


def _stored_top(stored: StoredTop, page: Page) -> TopLists:
    """Return the page's stored top lists; raises NoStoredMusicError if there are none."""
    lists = (getattr(stored, page.service.stored_as) or {}).get(page.time_range)
    if lists is None:
        msg = f"No {page.time_range} top lists for {page.service.name} in the store"
        raise NoStoredMusicError(msg)
    return lists


class AppleMusicClient(SpotifyClient):
    """The Spotify area with only Apple Music on: the same, but its messages name Apple Music, not Spotify."""

    panel_title = N_("Apple Music")
    panel_logo = None


def music_client_class(config: MusicSettings) -> type[SpotifyClient]:
    """Return the client for the services the Spotify area is configured to show."""
    return AppleMusicClient if config.services == (APPLE_MUSIC,) else SpotifyClient
