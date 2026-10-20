"""From the panels the clients made to the picture on the screen.

A layout, picked for every frame from the screen's canvas and what the device can show, draws it.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.rendering.layout import Box, Frame, Layout
from countdown_core.rendering.layouts.classic import CLASSIC
from countdown_core.rendering.layouts.classic_portrait import CLASSIC_PORTRAIT
from countdown_core.rendering.registry import LayoutRegistry
from countdown_core.rendering.screen import Canvas, canvas_for, native_size
from countdown_core.system_screens.empty_panel import EmptyPanel
from countdown_core.system_screens.message_panel import MessagePanel

if TYPE_CHECKING:
    from countdown_api.models import Screen
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

logger = logging.getLogger(__name__)

LAYOUTS = LayoutRegistry(CLASSIC + CLASSIC_PORTRAIT)

# With Spotify there too, the developer panel takes Spotify's area one minute in this many. Not two: Spotify turns a
# page every minute, so every other minute would always land on the same pages.
DEVELOPER_TURN = 3


def layout_for(screen: Screen, available: frozenset[ClientClasses]) -> tuple[Layout, Canvas]:
    """Pick the layout to draw with, and the canvas it draws on.

    The canvas is the screen as it's hung, or the default canvas if no layout draws on that yet.
    """
    canvas = LAYOUTS.drawable(canvas_for(screen))
    if ClientClasses.DEVELOPER in available:
        # It's drawn in Spotify's area (see share_spotify_area), so the layouts lay that out.
        available |= {ClientClasses.SPOTIFY}
    # No layout choice in the config yet (the contract has no field for it): the canvas's first.
    return LAYOUTS.select(canvas, None, available), canvas


def render(
    panels: dict[str, Panel],
    screen: Screen,
    available: frozenset[ClientClasses],
    *,
    clock_format: ClockFormat = "24h",
    now: datetime | None = None,
) -> Frame:
    """Draw the frame for `screen`, upright as the viewer sees it, at `now` (the time, if not given).

    If every panel is a MessagePanel (or has nothing to draw), it's the "nothing to show yet" screen instead of a
    layout full of messages.
    """
    if all(isinstance(panel, MessagePanel) or not panel.fitted_height(1) for panel in panels.values()):
        return Frame(EmptyPanel().render(*canvas_for(screen).size))
    panels = share_spotify_area(panels, available, now or datetime.now())  # noqa: DTZ005 -- only the minute matters
    layout, canvas = layout_for(screen, available)
    logger.debug("Drawing %s on %s", type(layout).__name__, canvas)
    return layout.render(panels, canvas, clock_format=clock_format)


def share_spotify_area(
    panels: dict[str, Panel], available: frozenset[ClientClasses], now: datetime
) -> dict[str, Panel]:
    """Return `panels` with the developer panel in Spotify's area when it's the developer panel's turn.

    That's always without Spotify, and with it one minute in DEVELOPER_TURN, every minute while it shows a release,
    or whenever Spotify has only a message to show (nothing playing, not linked).
    """
    developer = panels.get(ClientClasses.DEVELOPER.api_name)
    if ClientClasses.DEVELOPER not in available or developer is None:
        return panels
    spotify = panels.get(ClientClasses.SPOTIFY.api_name)
    if (
        ClientClasses.SPOTIFY in available
        and not isinstance(spotify, MessagePanel)
        and not (isinstance(developer, DeveloperPanel) and developer.has_release)
        and now.minute % DEVELOPER_TURN != DEVELOPER_TURN - 1
    ):
        return panels
    return panels | {ClientClasses.SPOTIFY.api_name: developer}


def to_native(frame: Frame, screen: Screen) -> Frame:
    """`frame` as the panel takes it, in the panel's landscape picture.

    A portrait frame is turned a quarter anticlockwise, and its volatile box with it. A landscape one is already there.
    """
    width, height = frame.image.size
    if (width, height) == native_size(screen):
        return frame
    image = frame.image.transpose(Image.Transpose.ROTATE_90)
    return Frame(image, None if frame.volatile is None else _rotated(frame.volatile, width))


def _rotated(box: Box, width: int) -> Box:
    """`box` in a picture `width` wide, after the picture is turned a quarter anticlockwise.

    A point (x, y) lands at (y, width - x).
    """
    x0, y0, x1, y1 = box
    return y0, width - x1, y1, width - x0
