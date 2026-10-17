"""The developer panel's client: the owner's GitHub pull requests and builds, and AI plans, from the broker."""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, override

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.models import DeveloperSummary
from countdown_core.developer.notices import DeveloperNoticeSource
from countdown_core.i18n import N_
from countdown_core.store import keys, reader
from countdown_core.store.developer import StoredDeveloper

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.core.style import Style
    from countdown_core.notices.notice import NoticeSource
    from countdown_credentials.registration import Registration


class NoStoredDeveloperError(LookupError):
    """The store has nothing for the device: the developer provider is down, or hasn't fetched it yet."""


@dataclass(frozen=True)
class DeveloperSettings:
    """What the developer client is built with: whether the config switches it on, and the style to draw it in."""

    enabled: bool
    style: Style


class DeveloperClient(AbstractClient):
    """GitHub and the AI plans, from auth-broker, which holds the GitHub token and makes the calls.

    So this device never holds a token. A renderer that reads the store (countdown_core.store.reader) reads what the
    broker's developer provider wrote there, as often as the screen refreshes: the provider polls, and asks for a
    faster refresh itself while a release runs (the pace key, see pace_keys). One that doesn't asks the broker's
    /api/developer, authenticating as the device `registration` registered.
    """

    # Asking the broker: pull requests and builds don't change by the minute, and a poll costs it a few GitHub calls.
    poll_interval = timedelta(minutes=5)
    # While a release runs, its jobs do.
    release_poll_interval = timedelta(minutes=1)
    # Reading the store costs nothing: whenever the screen refreshes.
    stored_poll_interval = timedelta(0)
    panel_title = N_("Developer")

    def __init__(self, registration: Registration, config: DeveloperSettings) -> None:
        """`registration`: this device at the broker, which it calls as; `config`: the developer settings."""
        super().__init__(registration, config)
        self.config: DeveloperSettings = config
        self.session.auth = registration.auth
        # The last answer, which the notice source reads too.
        self.summary: DeveloperSummary | None = None
        if self._stored_key() is not None:
            self.poll_interval = self.stored_poll_interval
        self._notice_sources: list[NoticeSource] = [DeveloperNoticeSource(self.session, self)]

    @override
    def _initialise(self) -> None:
        pass

    @override
    def _update(self) -> Panel:
        if self._stored_key() is not None:
            self.summary = self._read_store()
        else:
            self.summary = self._ask_broker()
        return DeveloperPanel(self.summary, self.config.style)

    def _stored_key(self) -> str | None:
        """Return the store key of this device's data; None if this renderer reads no store (and so asks the broker)."""
        # Only a renderer's registration (RendererRegistration) carries the device id its keys are under.
        device_id = getattr(self.registration, "device_id", None)
        return None if reader.store() is None or device_id is None else keys.developer(device_id)

    def _read_store(self) -> DeveloperSummary:
        """Read what the developer provider stored.

        Raises NoStoredDeveloperError if there's nothing, and RedisError if the store can't be asked.
        """
        store, key = reader.store(), self._stored_key()
        stored = reader.read(store, key, StoredDeveloper)
        if stored is None:
            msg = f"No developer data in the store under {key}"
            raise NoStoredDeveloperError(msg)
        return stored

    def _ask_broker(self) -> DeveloperSummary:
        response = self.session.get(f"{self.registration.broker_url}/api/developer", timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        summary = DeveloperSummary.model_validate_json(response.content)
        running = any(release.state == "running" for release in summary.releases)
        self.poll_interval = self.release_poll_interval if running else type(self).poll_interval
        return summary

    @override
    def pace_keys(self) -> list[str]:
        """Return the key where the developer provider asks for a faster refresh while a release runs."""
        device_id = getattr(self.registration, "device_id", None)
        return [] if self._stored_key() is None else [keys.pace(keys.DEVELOPER, device_id)]

    @override
    def notice_sources(self) -> list[NoticeSource]:
        """Return the source of the panel's notices: a build failing, a plan renewing, a plan nearly used up."""
        return self._notice_sources
