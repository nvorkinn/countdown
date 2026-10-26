"""The developer panel's client: the owner's GitHub pull requests and builds, and AI plans, from the store."""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, override

from countdown_renderer.abstract_client import AbstractClient
from countdown_renderer.developer.developer_panel import DeveloperPanel
from countdown_renderer.developer.notices import DeveloperNoticeSource
from countdown_shared.i18n import N_
from countdown_shared.store import developer, reader

if TYPE_CHECKING:
    from countdown_renderer.notices.notice import NoticeSource
    from countdown_renderer.panel import Panel
    from countdown_renderer.style import Style


@dataclass(frozen=True)
class DeveloperSettings:
    """What the developer client is built with: whether the config switches it on, and the style to draw it in."""

    enabled: bool
    style: Style


class DeveloperClient(AbstractClient[DeveloperSettings]):
    """GitHub and the AI plans, from the store (`v1:developer:<device id>`).

    The broker's developer provider writes it there: it holds the GitHub token and makes the calls, so this renderer
    never holds a token nor asks the broker. The provider polls, and asks for a faster refresh itself while a release
    runs (its pace key, which the broker reads, and a render job for each of its screens on every cycle).
    """

    # Reading the store costs nothing: it is read on every render.
    poll_interval = timedelta(0)
    panel_title = N_("Developer")

    def __init__(self, device_id: str, config: DeveloperSettings) -> None:
        """`device_id`: this screen, whose id the keys are under; `config`: the developer settings.

        Raises without a store.
        """
        super().__init__(device_id, config)
        self.config: DeveloperSettings = config
        self.store = reader.require_store()
        # Its notices read the store themselves, so they're as of the board's drawing, not this panel's last poll.
        self._notice_sources: list[NoticeSource] = [DeveloperNoticeSource(self.store, self.device_id)]

    @override
    def _initialise(self) -> None:
        pass

    @override
    def _update(self) -> Panel:
        """Return the panel drawn from what the developer provider stored.

        Raises NoStoredDeveloperError if there's nothing (so the panel says it's not available), and RedisError if the
        store can't be asked (the render fails, keeping the screen's frame).
        """
        return DeveloperPanel(developer.read(self.store, self.device_id), self.config.style)

    @override
    def notice_sources(self) -> list[NoticeSource]:
        """Return the source of the panel's notices: a build failing, a plan renewing, a plan nearly used up."""
        return self._notice_sources
