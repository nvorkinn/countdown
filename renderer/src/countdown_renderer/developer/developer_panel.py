"""The developer panel: GitHub on the left, the AI plans on the right; or a release's progress, while there's one."""

from typing import TYPE_CHECKING, override

from countdown_renderer import moment
from countdown_renderer.developer import release_styles
from countdown_renderer.developer.developer_styles import COMPACT_HEIGHT, COMPACT_STYLES, STYLES
from countdown_renderer.panel import Panel

if TYPE_CHECKING:
    from datetime import datetime

    from PIL import Image

    from countdown_core.developer.models import DeveloperSummary
    from countdown_renderer.style import Style
    from countdown_renderer.utils.clock import ClockFormat


class DeveloperPanel(Panel):
    """The owner's pull requests and failing builds beside their AI plans, or the latest release as it goes."""

    scales = True

    def __init__(
        self, summary: DeveloperSummary, style: Style = "hero", now: datetime | None = None, *, compact: bool = False
    ) -> None:
        """`summary`: what the store holds; `style`: how to draw it; `now`: when to draw it (default: the render's).

        `compact`: whether it's the strip above Spotify (see compacted), not the whole area.
        """
        self.summary = summary
        self.style = style
        self.now = now
        self.compact = compact

    def compacted(self) -> DeveloperPanel:
        """Return the panel as the strip above Spotify's song: the pull requests and builds, or a release's jobs."""
        return DeveloperPanel(self.summary, self.style, self.now, compact=True)

    @property
    def has_release(self) -> bool:
        """Whether there's a release to show, running or just finished, in place of GitHub and the plans."""
        return bool(self.summary.releases)

    @override
    def fitted_height(self, max_height: int) -> int:
        if not self.compact:
            return max_height
        return min(release_styles.COMPACT_HEIGHT if self.has_release else COMPACT_HEIGHT, max_height)

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        releases = self.summary.releases
        if releases:
            now = self.now or moment.now()
            styles = release_styles.COMPACT_STYLES if self.compact else release_styles.STYLES
            return styles[self.style](releases[0], len(releases) - 1, now, image_width, image_height)
        styles = COMPACT_STYLES if self.compact else STYLES
        return styles[self.style](self.summary, image_width, image_height)
