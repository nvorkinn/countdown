"""The developer panel's client: the owner's GitHub pull requests and builds, and AI plans, from the broker."""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, override

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.models import DeveloperSummary
from countdown_core.developer.notices import DeveloperNoticeSource
from countdown_core.i18n import N_

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.core.style import Style
    from countdown_core.notices.notice import NoticeSource
    from countdown_credentials.registration import Registration


@dataclass(frozen=True)
class DeveloperSettings:
    """What the developer client is built with: whether the config switches it on, and the style to draw it in."""

    enabled: bool
    style: Style


class DeveloperClient(AbstractClient):
    """GitHub and the AI plans, via auth-broker, which holds the GitHub token and makes the calls.

    So this device never holds a token. Authenticates as the device `registration` registered.
    """

    # Pull requests and builds don't change by the minute, and every poll costs the broker a few GitHub calls.
    poll_interval = timedelta(minutes=5)
    panel_title = N_("Developer")

    def __init__(self, registration: Registration, config: DeveloperSettings) -> None:
        """`registration`: this device at the broker, which it calls as; `config`: the developer settings."""
        super().__init__(registration, config)
        self.config: DeveloperSettings = config
        self.session.auth = registration.auth
        # The last answer, which the notice source reads too.
        self.summary: DeveloperSummary | None = None
        self._notice_sources: list[NoticeSource] = [DeveloperNoticeSource(self.session, self)]

    @override
    def _initialise(self) -> None:
        pass

    @override
    def _update(self) -> Panel:
        response = self.session.get(f"{self.registration.broker_url}/api/developer", timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        self.summary = DeveloperSummary.model_validate_json(response.content)
        return DeveloperPanel(self.summary, self.config.style)

    @override
    def notice_sources(self) -> list[NoticeSource]:
        """Return the source of the panel's notices: a build failing, a plan renewing, a plan nearly used up."""
        return self._notice_sources
