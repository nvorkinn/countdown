"""The developer panel's client: the owner's GitHub pull requests and builds, and AI plans, from the store."""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, override

from countdown_core.core.abstract_client import AbstractClient
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.notices import DeveloperNoticeSource
from countdown_core.i18n import N_
from countdown_core.store import keys, reader
from countdown_core.store.developer import StoredDeveloper

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.core.style import Style
    from countdown_core.developer.models import DeveloperSummary
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
    """GitHub and the AI plans, from the store (`v1:developer:<device id>`).

    auth-broker's developer provider writes it there: it holds the GitHub token and makes the calls, so this renderer
    never holds a token nor asks the broker. The provider polls, and asks for a faster refresh itself while a release
    runs (the pace key, see pace_keys).
    """

    # Reading the store costs nothing: whenever the screen refreshes.
    poll_interval = timedelta(0)
    panel_title = N_("Developer")

    def __init__(self, registration: Registration, config: DeveloperSettings) -> None:
        """`registration`: this device, whose id the keys are under; `config`: the developer settings.

        Raises without a store, or without a device id to read the panel by.
        """
        super().__init__(registration, config)
        self.config: DeveloperSettings = config
        self.store = reader.require_store()
        self.device_id = reader.device_id(registration)
        # The last answer, which the notice source reads too.
        self.summary: DeveloperSummary | None = None
        self._notice_sources: list[NoticeSource] = [DeveloperNoticeSource(self.session, self)]

    @override
    def _initialise(self) -> None:
        pass

    @override
    def _update(self) -> Panel:
        """Return the panel drawn from what the developer provider stored.

        Raises NoStoredDeveloperError if there's nothing (keeping the last panel), and RedisError if the store can't
        be asked.
        """
        stored = reader.read(self.store, keys.developer(self.device_id), StoredDeveloper)
        if stored is None:
            msg = f"No developer data in the store for device {self.device_id}"
            raise NoStoredDeveloperError(msg)
        self.summary = stored
        return DeveloperPanel(self.summary, self.config.style)

    @override
    def pace_keys(self) -> list[str]:
        """Return the key where the developer provider asks for a faster refresh while a release runs."""
        return [keys.pace(keys.DEVELOPER, self.device_id)]

    @override
    def notice_sources(self) -> list[NoticeSource]:
        """Return the source of the panel's notices: a build failing, a plan renewing, a plan nearly used up."""
        return self._notice_sources
