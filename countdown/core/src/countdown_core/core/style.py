"""The one look every panel is drawn in (the config's style)."""

from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from countdown_api.models import ConfigResponse

Style = Literal["airy", "hero", "card"]

# What an older broker may still send in a panel's own style field, and the style each is drawn as: the retired
# classic, the departures' grouped (now airy) and the notice board's boxed (dropped for card, its rounded cousin).
_RENAMED: dict[str, Style] = {"classic": "hero", "grouped": "airy", "boxed": "card"}


def normalise(style: str) -> Style:
    """Return the style `style` is drawn as, mapping the old names onto the shared ones."""
    return _RENAMED.get(style, cast("Style", style))


def panel_style(config: ConfigResponse, own: str) -> Style:
    """Return the style to draw a panel in: the config's shared one, or, from a broker that predates it, `own`.

    `own` is the panel's own style field (tfl.style, spotify.style and so on).
    """
    return normalise(config.style if config.style is not None else own)
