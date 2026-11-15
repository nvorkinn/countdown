"""The one look every panel is drawn in (the config's style)."""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from countdown_api.models import EpaperConfig

Style = Literal["airy", "hero", "card"]


def panel_style(config: EpaperConfig) -> Style:
    """Return the style to draw a panel in: the config's one, which every panel shares (its style's `drawing`)."""
    return config.style.drawing
