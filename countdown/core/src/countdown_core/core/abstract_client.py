import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import StrEnum, auto
from typing import TYPE_CHECKING

import requests
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from countdown_core.system_screens.message_panel import MessagePanel

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)

# A client call slower than this is logged, so a slow API shows up in the journal by name
# instead of just making the whole cycle late.
SLOW_CALL_S = 5

DEFAULT_TIMEOUT = 10


class ClientStatus(StrEnum):
    DISABLED = auto()
    UNINITIALISED = auto()
    INITIALISING = auto()
    CONNECTED = auto()
    ERROR = auto()
    FATAL = auto()


class AbstractClient(ABC):
    """Base for the API clients: a shared retrying session and a per-endpoint cache for
    change detection (see https://github.com/nvorkinn/pi-dashboard/issues/8 for wider use).

    _initialise() and _update() are blocking and run in a worker thread, so they must not
    touch anything main-thread-only: build the panel's data there and leave rendering
    (Pillow, SPI) to the display.
    """

    poll_interval: timedelta = timedelta(minutes=1)
    # Title/logo for a MessagePanel standing in for this client's panel.
    panel_title: str = ""
    panel_logo: str | None = None

    def __init__(self, registration: Registration, config: object | None = None):
        # The device, as the broker knows it. Only the clients that call the broker send its
        # secret (session.auth): it mustn't go to third-party APIs.
        self.registration = registration
        self.config = config
        self.session = self._build_retrying_session()
        self.cache: dict[str, object] = {}
        self.status: ClientStatus = ClientStatus.UNINITIALISED
        self.last_updated: datetime | None = None

    def __del__(self):
        self.session.close()

    async def initialise(self) -> None:
        """Runs _initialise() and tracks the outcome in self.status (ERROR, re-raised, on
        failure). A disabled client makes no calls.
        """
        if self.is_disabled():
            return
        self.status = ClientStatus.INITIALISING
        started = time.monotonic()
        try:
            await asyncio.to_thread(self._initialise)
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
        """A requests.Session that retries connection errors and 502/503/504 with backoff."""
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
            logger.warning(f"{type(self).__name__}: {what} took {elapsed:.1f}s")

    def needs_refresh(self, new_config: object) -> bool:
        """Whether `new_config` means this client must be rebuilt. Defaults to any change;
        override to keep state (tokens, caches) across changes that don't affect it.
        """
        return self.config != new_config

    def is_disabled(self) -> bool:
        return self.status == ClientStatus.DISABLED

    @property
    def is_due(self) -> bool:
        if self.last_updated is None:
            return True
        return datetime.now() - self.last_updated >= self.poll_interval

    async def update(self) -> Panel | None:
        """Polls the API and returns its panel, initialising first if not yet CONNECTED.
        last_updated is stamped even when that fails, so the retry waits a full
        poll_interval (Glowmarkt rate-limits bad logins).
        """
        if self.is_disabled():
            panel = None
        else:
            if self.status in (ClientStatus.UNINITIALISED, ClientStatus.ERROR):
                try:
                    await self.initialise()
                except Exception:
                    self.last_updated = datetime.now()
                    raise
            started = time.monotonic()
            try:
                panel = await asyncio.to_thread(self._update)
            finally:
                self._log_if_slow("update", started)
        self.last_updated = datetime.now()
        return panel

    @abstractmethod
    def _update(self) -> Panel | None:
        pass

    @classmethod
    def message_panel(cls, message: str) -> MessagePanel:
        return MessagePanel(cls.panel_title, message, cls.panel_logo)

    def _cache_and_compare(self, endpoint: str, data: object) -> bool:
        """True if the last call to the same endpoint returned the same data. Always False
        the first time an endpoint is seen, even if that value is None.
        """
        is_first_time = endpoint not in self.cache
        cached = self.cache.get(endpoint)
        self.cache[endpoint] = data
        if is_first_time:
            return False
        return cached == data
