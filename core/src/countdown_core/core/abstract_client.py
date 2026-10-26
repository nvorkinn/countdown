"""The base class of the API clients, and the states a client goes through."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from countdown_core.i18n import N_
from countdown_core.store.reader import NoStoredDataError
from countdown_core.system_screens.message_panel import MessagePanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.notices.notice import NoticeSource

logger = logging.getLogger(__name__)

# A client call slower than this is logged, so a slow API shows up in the journal by name
# instead of just making the whole render late.
SLOW_CALL_S = 5

DEFAULT_TIMEOUT = 10

# What a panel says when the store has nothing for it: its key has expired (the provider is down), or isn't there yet.
NOT_AVAILABLE = N_("Not available")


class ClientStatus(StrEnum):
    """Where a client stands, as the metrics report it for each API."""

    DISABLED = auto()
    UNINITIALISED = auto()
    INITIALISING = auto()
    CONNECTED = auto()
    ERROR = auto()
    FATAL = auto()


class AbstractClient[C = Any](ABC):
    """Base for the API clients: a shared retrying session and a per-endpoint cache for change detection.

    `C` is the type of the client's config: its section of the broker's config.

    _initialise() and _update() are blocking, and run in whatever thread the render runs in (initialise() and update()
    run them in a worker thread): build the panel's data there, and leave drawing it to the layout.
    """

    poll_interval: timedelta = timedelta(minutes=1)
    # Title/logo for a MessagePanel standing in for this client's panel.
    panel_title: str = ""
    panel_logo: str | None = None

    def __init__(self, device_id: str, config: C) -> None:
        """`device_id` is the screen, as the broker knows it; `config`, this API's section of the broker's config."""
        # The screen whose keys in the store the client reads (the ones by device: energy, music, developer).
        self.device_id = device_id
        self.config: C = config
        self.session = self._build_retrying_session()
        self.cache: dict[str, object] = {}
        self.status: ClientStatus = ClientStatus.UNINITIALISED
        self.last_updated: datetime | None = None
        # Whether the last poll drew "Not available" because the store had nothing for it (its key had expired).
        self.expired = False

    def __del__(self) -> None:
        """Close the session's connections."""
        self.session.close()

    async def initialise(self) -> None:
        """Run initialise_now() in a worker thread."""
        await asyncio.to_thread(self.initialise_now)

    def initialise_now(self) -> None:
        """Run _initialise() and track the outcome in self.status (ERROR, re-raised, on failure). Blocking.

        A disabled client makes no calls.
        """
        if self.is_disabled():
            return
        self.status = ClientStatus.INITIALISING
        started = time.monotonic()
        try:
            self._initialise()
        except Exception:
            self.status = ClientStatus.ERROR
            raise
        finally:
            self._log_if_slow("initialise", started)
        self.status = ClientStatus.CONNECTED

    @abstractmethod
    def _initialise(self) -> None:
        pass

    @staticmethod
    def _build_retrying_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
        """Build a requests.Session that retries connection errors and 502/503/504 with backoff."""
        session = requests.Session()
        retry = Retry(
            total=retries,
            backoff_factor=backoff_factor,
            status_forcelist=[502, 503, 504],
            allowed_methods=None,  # retry on every method, including the POST used for auth
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _log_if_slow(self, what: str, started: float) -> None:
        elapsed = time.monotonic() - started
        if elapsed >= SLOW_CALL_S:
            logger.warning("%s: %s took %.1fs", type(self).__name__, what, elapsed)

    def needs_refresh(self, new_config: C) -> bool:
        """Whether `new_config` means this client must be rebuilt.

        Defaults to any change; override to keep state (tokens, caches) across changes that don't affect it.
        """
        return self.config != new_config

    def notice_sources(self) -> list[NoticeSource]:
        """Return the sources of notices about what this client shows, for the notice board to poll and rank.

        None by default; the departures give their stops' disruptions. Built with the client, so a rebuild gives new
        ones.
        """
        return []

    def is_disabled(self) -> bool:
        """Whether the client is switched off, and so makes no calls."""
        return self.status == ClientStatus.DISABLED

    @property
    def is_due(self) -> bool:
        """Whether a full poll_interval has passed since the last update (always so before the first)."""
        if self.last_updated is None:
            return True
        return datetime.now() - self.last_updated >= self.poll_interval  # noqa: DTZ005 -- last_updated is naive local time, as callers set it; only the elapsed interval matters

    async def update(self) -> Panel | None:
        """Run poll() in a worker thread."""
        return await asyncio.to_thread(self.poll)

    def poll(self) -> Panel | None:
        """Read the API's data and return its panel, initialising first if not yet CONNECTED. Blocking.

        If the store has nothing for it (a NoStoredDataError: the provider's key has expired, or isn't there yet), the
        panel says "Not available", and `expired` is set: the provider's TTL is the only expiry there is, so nothing
        older is drawn. Anything else that fails, Redis not answering among them, raises (see ApiRegistry.poll).

        last_updated is stamped even when that fails.
        """
        self.expired = False
        if self.is_disabled():
            panel = None
        else:
            if self.status in (ClientStatus.UNINITIALISED, ClientStatus.ERROR):
                try:
                    self.initialise_now()
                except Exception:
                    self.last_updated = datetime.now()  # noqa: DTZ005 -- last_updated is naive local time, as callers set it; only the elapsed interval matters
                    raise
            started = time.monotonic()
            try:
                panel = self._update()
            except NoStoredDataError as e:
                # Expected while a provider is down, so no traceback; the message names no place (see its raisers).
                logger.warning("%s: %s; its panel says it's not available", type(self).__name__, e)
                panel = self.message_panel(NOT_AVAILABLE)
                self.expired = True
            finally:
                self._log_if_slow("update", started)
        self.last_updated = datetime.now()  # noqa: DTZ005 -- last_updated is naive local time, as callers set it; only the elapsed interval matters
        return panel

    @abstractmethod
    def _update(self) -> Panel | None:
        pass

    @classmethod
    def message_panel(cls, message: str) -> MessagePanel:
        """Build a MessagePanel saying `message`, to stand in for this client's panel."""
        return MessagePanel(cls.panel_title, message, cls.panel_logo)

    def _cache_and_compare(self, endpoint: str, data: object) -> bool:
        """Remember `data` for `endpoint`, and say whether the last call to it returned the same.

        Always False the first time an endpoint is seen, even if that value is None.
        """
        is_first_time = endpoint not in self.cache
        cached = self.cache.get(endpoint)
        self.cache[endpoint] = data
        if is_first_time:
            return False
        return cached == data
