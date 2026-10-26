"""The one look every panel is drawn in (the config's style)."""

from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from countdown_api.models import ConfigResponse

Style = Literal["airy", "hero", "card"]

# The names an older broker may still send in a panel's own style field, and the shared style each is drawn as.
_RENAMED: dict[str, Style] = {"classic": "hero", "grouped": "airy", "boxed": "card"}


def normalise(style: str) -> Style:
    """Return the style `style` is drawn as, mapping the older names onto the shared ones."""
    return _RENAMED.get(style, cast("Style", style))


def panel_style(config: ConfigResponse, own: str) -> Style:
    """Return the style to draw a panel in: the config's shared one, or, if the broker sends none, `own`.

    `own` is the panel's own style field (tfl.style, spotify.style and so on).
    """
    return normalise(config.style if config.style is not None else own)
