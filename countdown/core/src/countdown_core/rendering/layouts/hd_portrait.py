"""The classic portrait dashboard on a 1872x1404 HD screen hung portrait (1404x1872).

The panels draw in pixels sized for the 7.5" screens, so at this screen's density (about 227 dpi) they'd be a third
as big. Instead the portrait layout draws at half the size, on 702x936, and the frame is scaled up twice: the text
comes out as big as on the 800x480 screens, and the extra room shows four stops, a 2x2 grid, instead of two.
"""

from typing import TYPE_CHECKING, override

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.layouts.classic_portrait import (
    ENERGY_ROW_SAVING,
    BasePortraitLayout,
    ClassicPortraitLayout,
    GlowPortraitLayout,
    SpotifyGlowPortraitLayout,
    SpotifyPortraitLayout,
)
from countdown_core.rendering.screen import Canvas
from countdown_core.tfl.combined_arrival_panel import COLUMN_GAP, COLUMNS, CombinedArrivalPanel
from countdown_core.utils.utils import GAP

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

HD_PORTRAIT = Canvas(1404, 1872)
# How many screen pixels each of the layout's takes, either way.
SCALE = 2
# What the layout draws on, before it's scaled up.
LOGICAL = Canvas(HD_PORTRAIT.width // SCALE, HD_PORTRAIT.height // SCALE)
# The stops share the whole width, rather than keep the 7.5" screens' width with room to spare beside them.
STOP_WIDTH = (LOGICAL.width - 2 * GAP - (COLUMNS - 1) * COLUMN_GAP) // COLUMNS
# Two rows of stops, each as tall as the portrait screen's one.
ARRIVALS_HEIGHT = 2 * 165


class HdPortraitLayout(ClassicPortraitLayout):
    """The portrait layout drawn at half the canvas and scaled up; the variants say what shows, as on portrait."""

    canvases = frozenset({HD_PORTRAIT})
    stops_shown = 4
    ARRIVALS_HEIGHT = ARRIVALS_HEIGHT

    @override
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        arrivals = panels[ClientClasses.TFL.api_name]
        if isinstance(arrivals, CombinedArrivalPanel):
            panels = panels | {ClientClasses.TFL.api_name: arrivals.widened(STOP_WIDTH)}
        drawn = super().render(panels, LOGICAL, clock_format=clock_format)
        # Each pixel doubled, not smoothed: the 1-bit text keeps its edges, with no grey for the e-paper to dither.
        return Frame(drawn.image.resize(canvas.size, Image.Resampling.NEAREST))


class SpotifyGlowHdPortraitLayout(HdPortraitLayout, SpotifyGlowPortraitLayout):
    """Everything: the energy chart beside the compact weather, Spotify between the stops and the notices."""

    ARRIVALS_HEIGHT = ARRIVALS_HEIGHT + ENERGY_ROW_SAVING


class SpotifyHdPortraitLayout(HdPortraitLayout, SpotifyPortraitLayout):
    """With Spotify, and the full weather (Glowmarkt isn't set up)."""


class GlowHdPortraitLayout(HdPortraitLayout, GlowPortraitLayout):
    """No Spotify: the energy chart beside the compact weather, and Spotify's room goes to the stops and notices."""

    ARRIVALS_HEIGHT = ARRIVALS_HEIGHT + 60 + ENERGY_ROW_SAVING


class BaseHdPortraitLayout(HdPortraitLayout, BasePortraitLayout):
    """Neither: the full weather, and Spotify's room goes to the stops and the notices."""

    ARRIVALS_HEIGHT = ARRIVALS_HEIGHT + 60


# The first is what's drawn if no variant fits (never, while the four cover every combination).
HD_PORTRAIT_LAYOUTS = [
    SpotifyGlowHdPortraitLayout(),
    SpotifyHdPortraitLayout(),
    GlowHdPortraitLayout(),
    BaseHdPortraitLayout(),
]
