"""From the panels the clients made to the picture on the screen: a layout, picked every cycle
from the screen's canvas and what the device can show, draws the frame."""

from countdown_core.core.panel import Panel
from countdown_core.rendering.layout import Frame, Layout
from countdown_core.rendering.layouts.classic import CLASSIC
from countdown_core.rendering.registry import LayoutRegistry
from countdown_core.rendering.screen import Canvas
from countdown_core.system_screens.empty_panel import EmptyPanel
from countdown_core.system_screens.message_panel import MessagePanel

LAYOUTS = LayoutRegistry(CLASSIC)


def render(layout: Layout, panels: dict[str, Panel], canvas: Canvas) -> Frame:
    """The frame for this cycle. If every panel is a MessagePanel, it's the "nothing to show
    yet" screen instead of a layout full of messages."""
    if all(isinstance(panel, MessagePanel) for panel in panels.values()):
        return Frame(EmptyPanel().render(*canvas.size))
    return layout.render(panels, canvas)
