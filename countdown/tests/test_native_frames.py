"""The frame as the panel takes it: drawn upright as the screen is hung, then turned into the panel's own picture.

rendering.render draws the screen's panels (or says there's nothing to show), and rendering.to_native turns a portrait
screen's picture a quarter into the panel's 800x480 landscape one, as render.render hands it to the record.
"""

import pytest
from PIL import Image

import countdown_core.core.api_registry  # noqa: F401 -- before the panels: the import order the render has
from countdown_api.models import Screen
from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering import render, to_native
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.screen import canvas_for
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel

pytestmark = pytest.mark.usefixtures("symbols_font")

LANDSCAPE = Screen(orientation="horizontal", width=800, height=480)
VERTICAL = Screen(orientation="vertical", width=800, height=480)


class Solid(Panel):
    """A panel that renders as one flat colour at a fixed size."""

    def __init__(self, color: str, size: tuple[int, int]):
        self.color, self.size = color, size

    def render(self, image_width: int, image_height: int, *, clock_format="24h") -> Image.Image:
        return Image.new("RGBA", self.size, self.color)


def dashboard() -> dict:
    return {
        "tfl": Solid("black", (300, 185)),
        "weather": Solid("gray", (200, 185)),
        "spotify": Solid("white", (536, 120)),
        "glowmarkt": Solid("white", (250, 280)),
    }


def drawn(panels: dict, screen: Screen = LANDSCAPE) -> tuple[Image.Image, Image.Image]:
    """Return the picture upright, as the viewer sees it, and as the panel takes it."""
    frame = render(panels, screen, frozenset(ClientClasses))
    return frame.image, to_native(frame, screen).image


def test_with_nothing_to_show_it_says_so_instead_of_a_blank_screen():
    upright, _ = drawn({})

    centre = upright.convert("L").crop((100, 190, 700, 290))
    assert centre.getextrema()[0] < 100  # dark text pixels, not just white


def test_messages_and_an_empty_notice_board_are_still_nothing_to_show():
    """The empty board is "all clear", not something to show."""
    panels = {api.api_name: MessagePanel("Title", "Not yet") for api in ClientClasses}
    panels[ClientClasses.NOTICE_BOARD.api_name] = NoticeBoardPanel([])

    assert drawn(panels)[0] == drawn({})[0]


def test_a_vertical_screen_gets_its_pairing_screen_upright_and_the_panel_its_own_landscape_picture():
    upright = PairingCodePanel("ABC123", "device-1", has_changed=True).render(*canvas_for(VERTICAL).size)

    native = to_native(Frame(upright), VERTICAL).image

    assert upright.size == (480, 800)
    assert native.size == (800, 480)


@pytest.mark.parametrize("panels", [{}, dashboard()], ids=["nothing to show", "dashboard"])
def test_a_vertical_screen_is_drawn_upright_and_the_panel_gets_its_own_landscape_picture(panels):
    upright, native = drawn(panels, VERTICAL)

    assert upright.size == (480, 800)
    assert native.size == (800, 480)
