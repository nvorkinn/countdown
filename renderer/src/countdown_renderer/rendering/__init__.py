"""From the panels the clients made to the picture on the screen.

A layout, picked for every frame from the screen's canvas and what the device can show, draws it.
"""

import logging
from typing import TYPE_CHECKING

from PIL import Image

from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.developer.developer_panel import DeveloperPanel
from countdown_renderer.rendering.layout import Frame, Layout
from countdown_renderer.rendering.layouts.classic import CLASSIC
from countdown_renderer.rendering.layouts.classic_portrait import CLASSIC_PORTRAIT
from countdown_renderer.rendering.layouts.hd_landscape import HD_LANDSCAPE_LAYOUTS
from countdown_renderer.rendering.layouts.hd_landscape_board import HD_LANDSCAPE_BOARD_LAYOUTS
from countdown_renderer.rendering.layouts.hd_landscape_column import HD_LANDSCAPE_COLUMN_LAYOUTS
from countdown_renderer.rendering.layouts.hd_landscape_focus import HD_LANDSCAPE_FOCUS_LAYOUTS
from countdown_renderer.rendering.layouts.hd_landscape_newspaper import HD_LANDSCAPE_NEWSPAPER_LAYOUTS
from countdown_renderer.rendering.layouts.hd_landscape_pages import HD_LANDSCAPE_PAGES_LAYOUTS
from countdown_renderer.rendering.layouts.hd_portrait import HD_PORTRAIT_LAYOUTS
from countdown_renderer.rendering.layouts.hd_portrait_designs import HD_PORTRAIT_DESIGNS
from countdown_renderer.rendering.registry import LayoutRegistry
from countdown_renderer.rendering.screen import Canvas, canvas_for, native_size
from countdown_renderer.spotify.spotify_panel import SpotifyPanel
from countdown_renderer.system_screens.empty_panel import EmptyPanel
from countdown_renderer.system_screens.message_panel import MessagePanel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from countdown_api.models import Screen
    from countdown_renderer.panel import Panel
    from countdown_renderer.utils.clock import ClockFormat

logger = logging.getLogger(__name__)

# Each canvas's first is its default: on the HD screen, the classic layout hung portrait and the sidebar hung
# landscape, each before its other designs (in the order the config page offers them).
LAYOUTS = LayoutRegistry(
    CLASSIC
    + CLASSIC_PORTRAIT
    + HD_PORTRAIT_LAYOUTS
    + HD_PORTRAIT_DESIGNS
    + HD_LANDSCAPE_LAYOUTS
    + HD_LANDSCAPE_BOARD_LAYOUTS
    + HD_LANDSCAPE_COLUMN_LAYOUTS
    + HD_LANDSCAPE_FOCUS_LAYOUTS
    + HD_LANDSCAPE_NEWSPAPER_LAYOUTS
    + HD_LANDSCAPE_PAGES_LAYOUTS
)


def layout_for(screen: Screen, available: frozenset[ClientClasses]) -> tuple[Layout, Canvas]:
    """Pick the layout to draw with, and the canvas it draws on.

    The canvas is the screen as it's hung, or the default canvas if no layout draws on that yet.
    """
    canvas = LAYOUTS.drawable(canvas_for(screen))
    if ClientClasses.DEVELOPER in available:
        # It's drawn in Spotify's area (see place_developer_panel), so the layouts lay that out.
        available |= {ClientClasses.SPOTIFY}
    # The config's choice (screen.layout), else the canvas's first; one the canvas doesn't have gets its first too.
    return LAYOUTS.select(canvas, screen.layout, available), canvas


def render(
    panels: dict[str, Panel],
    screen: Screen,
    available: frozenset[ClientClasses],
    *,
    clock_format: ClockFormat = "24h",
) -> Frame:
    """Draw the frame for `screen`, upright as the viewer sees it.

    If every panel is a MessagePanel (or has nothing to draw), it's the "nothing to show yet" screen instead of a
    layout full of messages.
    """
    if all(isinstance(panel, MessagePanel) or not panel.fitted_height(1) for panel in panels.values()):
        return Frame(EmptyPanel().render(*canvas_for(screen).size))
    panels = place_developer_panel(panels, available)
    layout, canvas = layout_for(screen, available)
    logger.debug("Drawing %s on %s", type(layout).__name__, canvas)
    return layout.render(panels, canvas, clock_format=clock_format)


def stops_fitting(panels: Mapping[str, Panel], screen: Screen, available: frozenset[ClientClasses]) -> int:
    """Say how many stops the frame has room for with these panels.

    That's the layout's stops_shown, or one row of them if the notices took the room a second row needs
    (Layout.stops_fitting).
    """
    layout, canvas = layout_for(screen, available)
    return layout.stops_fitting(place_developer_panel(panels, available), canvas)


def place_developer_panel(panels: Mapping[str, Panel], available: frozenset[ClientClasses]) -> dict[str, Panel]:
    """Return `panels` with the developer panel where the layouts draw it (see Layout._spotify_area).

    While Spotify shows a song, the developer panel keeps its own name and is drawn as a strip above it (or left out,
    if it has only a message). Otherwise (Spotify not set up, on a top list, or with only a message) the developer
    panel takes Spotify's place, whole.
    """
    developer = panels.get(ClientClasses.DEVELOPER.api_name)
    others = {name: panel for name, panel in panels.items() if name != ClientClasses.DEVELOPER.api_name}
    if ClientClasses.DEVELOPER not in available or developer is None:
        return others
    spotify = panels.get(ClientClasses.SPOTIFY.api_name)
    if ClientClasses.SPOTIFY not in available or not isinstance(spotify, SpotifyPanel):
        return others | {ClientClasses.SPOTIFY.api_name: developer}
    if isinstance(developer, DeveloperPanel):
        return others | {ClientClasses.DEVELOPER.api_name: developer.compacted()}
    return others


def to_native(frame: Frame, screen: Screen) -> Frame:
    """`frame` as the panel takes it, in the panel's landscape picture.

    A portrait frame is turned a quarter anticlockwise. A landscape one is already there.
    """
    width, height = frame.image.size
    if (width, height) == native_size(screen):
        return frame
    image = frame.image.transpose(Image.Transpose.ROTATE_90)
    return Frame(image)
