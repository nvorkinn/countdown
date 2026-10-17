"""The developer panel: GitHub on the left, the AI plans on the right."""

from typing import TYPE_CHECKING, override

from countdown_core.core.panel import Panel
from countdown_core.developer.developer_styles import STYLES

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.core.style import Style
    from countdown_core.developer.models import DeveloperSummary
    from countdown_core.utils.clock import ClockFormat


class DeveloperPanel(Panel):
    """The owner's pull requests and failing builds beside their AI plans."""

    def __init__(self, summary: DeveloperSummary, style: Style = "hero") -> None:
        """`summary`: the broker's answer; `style`: how to draw it."""
        self.summary = summary
        self.style = style

    @override
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        return STYLES[self.style](self.summary, image_width, image_height)
