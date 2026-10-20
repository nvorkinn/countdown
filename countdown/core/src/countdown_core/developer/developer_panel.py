"""The developer panel: GitHub on the left, the AI plans on the right; or a release's progress, while there's one."""

from typing import TYPE_CHECKING, override

from countdown_core.core import moment
from countdown_core.core.panel import Panel
from countdown_core.developer import release_styles
from countdown_core.developer.developer_styles import STYLES

if TYPE_CHECKING:
    from datetime import datetime

    from PIL import Image

    from countdown_core.core.style import Style
    from countdown_core.developer.models import DeveloperSummary
    from countdown_core.utils.clock import ClockFormat


class DeveloperPanel(Panel):
    """The owner's pull requests and failing builds beside their AI plans, or the latest release as it goes."""

    def __init__(self, summary: DeveloperSummary, style: Style = "hero", now: datetime | None = None) -> None:
        """`summary`: what the store holds; `style`: how to draw it; `now`: when to draw it (default: the render's)."""
        self.summary = summary
        self.style = style
        self.now = now

    @property
    def has_release(self) -> bool:
        """Whether there's a release to show, running or just finished: then it wants Spotify's area every minute."""
        return bool(self.summary.releases)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        releases = self.summary.releases
        if releases:
            now = self.now or moment.now()
            return release_styles.STYLES[self.style](releases[0], len(releases) - 1, now, image_width, image_height)
        return STYLES[self.style](self.summary, image_width, image_height)
