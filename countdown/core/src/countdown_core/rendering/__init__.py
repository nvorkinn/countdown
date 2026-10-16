"""From the panels the clients made to the picture on the screen: a layout, picked for every
frame from the screen's canvas and what the device can show, draws it."""

import logging

from PIL import Image

from countdown_api.models import Screen
from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.rendering.layout import Box, Frame, Layout
from countdown_core.rendering.layouts.classic import CLASSIC
from countdown_core.rendering.layouts.classic_portrait import CLASSIC_PORTRAIT
from countdown_core.rendering.registry import LayoutRegistry
from countdown_core.rendering.screen import Canvas, canvas_for, native_size
from countdown_core.system_screens.empty_panel import EmptyPanel
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.utils.clock import ClockFormat

logger = logging.getLogger(__name__)

LAYOUTS = LayoutRegistry(CLASSIC + CLASSIC_PORTRAIT)


def layout_for(screen: Screen, available: frozenset[ClientClasses]) -> tuple[Layout, Canvas]:
    """The layout to draw with, and the canvas it draws on: the screen as it's hung, or the
    default canvas if no layout draws on that yet."""
    canvas = LAYOUTS.drawable(canvas_for(screen))
    # No layout choice in the config yet (the contract has no field for it): the canvas's first.
    return LAYOUTS.select(canvas, None, available), canvas


def render(
    panels: dict[str, Panel],
    screen: Screen,
    available: frozenset[ClientClasses],
    *,
    clock_format: ClockFormat = "24h",
) -> Frame:
    """The frame for `screen`, upright as the viewer sees it. If every panel is a MessagePanel
    (or has nothing to draw), it's the "nothing to show yet" screen instead of a layout full
    of messages."""
    if all(isinstance(panel, MessagePanel) or not panel.fitted_height(1) for panel in panels.values()):
        return Frame(EmptyPanel().render(*canvas_for(screen).size))
    layout, canvas = layout_for(screen, available)
    logger.debug(f"Drawing {type(layout).__name__} on {canvas}")
    return layout.render(panels, canvas, clock_format=clock_format)


def to_native(frame: Frame, screen: Screen) -> Frame:
    """`frame` as the panel takes it: a portrait frame turned a quarter (anticlockwise, as the
    e-paper driver does for a portrait picture) into the panel's landscape picture, and its
    volatile box with it. A landscape frame is already there."""
    width, height = frame.image.size
    if (width, height) == native_size(screen):
        return frame
    image = frame.image.transpose(Image.Transpose.ROTATE_90)
    return Frame(image, None if frame.volatile is None else _rotated(frame.volatile, width))


def _rotated(box: Box, width: int) -> Box:
    """`box` in a picture `width` wide, after the picture is turned a quarter anticlockwise:
    a point (x, y) lands at (y, width - x)."""
    x0, y0, x1, y1 = box
    return y0, width - x1, y1, width - x0
