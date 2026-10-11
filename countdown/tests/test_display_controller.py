"""How DisplayController decides what to send its target: skip, partial or full refresh, what it
remembers about what's on the screen, and how it copes with a screen that can't take a picture."""

import logging
from types import SimpleNamespace

import pytest
from fake_target import FakeTarget
from PIL import Image

# countdown has to be imported before display.display, or it's a circular import.
import countdown_core.core.api_registry  # noqa: F401
from countdown_core.core import display as display_module
from countdown_core.core.display import FULL_REFRESH_INTERVAL_S, DisplayController
from countdown_core.core.panel import Panel
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_core.system_screens.setup_panel import SetupPanel

FULL = None  # a full refresh is a paint with no region


class Solid(Panel):
    """A panel that renders as one flat colour at a fixed size."""

    def __init__(self, color: str, size: tuple[int, int]):
        self.color, self.size = color, size

    def render(self, image_width: int, image_height: int) -> Image.Image:
        return Image.new("RGBA", self.size, self.color)


def frame(arrivals: str = "black", weather: str = "gray", arrivals_width: int = 300) -> dict:
    return {
        "tfl": Solid(arrivals, (arrivals_width, 185)),
        "weather": Solid(weather, (200, 185)),
        "spotify": Solid("white", (536, 120)),
        "glowmarkt": Solid("white", (250, 280)),
    }


def _pairing_panel() -> PairingCodePanel:
    return PairingCodePanel("ABC123", "device-1", has_changed=True)


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(value=1000.0)
    monkeypatch.setattr(display_module, "time", SimpleNamespace(monotonic=lambda: now.value))
    return now


def test_the_first_frame_is_one_full_refresh(clock):
    target = FakeTarget()

    DisplayController(target).display_screen(frame())

    assert target.paints == [FULL]


def test_an_unchanged_screen_is_left_alone(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame())

    controller.display_screen(frame())

    assert target.paints == [FULL]


def test_only_the_arrivals_changing_is_a_partial_refresh_of_just_their_area(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame(arrivals="black"))
    target.paints.clear()

    controller.display_screen(frame(arrivals="red"))

    # arrivals sit at (5, 5), 300x185; x rounded out to whole bytes
    assert target.paints == [(0, 5, 312, 190)]


@pytest.mark.parametrize("width", [300, 301, 307, 250])
def test_the_partial_region_is_byte_aligned_and_covers_the_arrivals(clock, width):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame("black", arrivals_width=width))

    controller.display_screen(frame("red", arrivals_width=width))

    x0, _, x1, _ = target.paints[-1]
    assert x0 % 8 == 0 and x1 % 8 == 0
    assert x0 <= 5 and x1 >= 5 + width


def test_a_change_outside_the_arrivals_is_a_full_refresh(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame(weather="gray"))
    target.paints.clear()

    controller.display_screen(frame(weather="red"))

    assert target.paints == [FULL]


def test_a_wider_arrivals_panel_moves_everything_beside_it_so_is_a_full_refresh(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame(arrivals_width=300))
    target.paints.clear()

    controller.display_screen(frame(arrivals="red", arrivals_width=350))

    assert target.paints == [FULL]


def test_partial_refreshes_give_way_to_a_full_one_after_the_interval_to_clear_ghosting(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame(arrivals="black"))

    clock.value += FULL_REFRESH_INTERVAL_S - 1
    target.paints.clear()
    controller.display_screen(frame(arrivals="red"))
    assert target.paints == [(0, 5, 312, 190)]

    clock.value += 2  # now more than the interval since the full refresh
    target.paints.clear()
    controller.display_screen(frame(arrivals="black"))
    assert target.paints == [FULL]

    clock.value += 1  # ...and that reset the clock
    target.paints.clear()
    controller.display_screen(frame(arrivals="red"))
    assert target.paints == [(0, 5, 312, 190)]


def test_a_failed_paint_forgets_what_is_on_the_screen_so_the_next_frame_is_repainted_in_full(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    target.fail = True
    with pytest.raises(RuntimeError):
        controller.display_screen(frame())

    target.fail = False
    controller.display_screen(frame())  # same picture, but the screen's state is unknown

    assert target.paints == [FULL]


def test_a_frame_that_never_reached_a_missing_screen_is_painted_once_one_appears(clock):
    target = FakeTarget(accepts=False)
    controller = DisplayController(target)
    controller.display_screen(frame())

    target.accepts = True
    controller.display_screen(frame())

    assert target.paints == [FULL]


def test_a_screen_that_cant_take_a_picture_still_gets_the_picture_composed_and_returned(clock):
    controller = DisplayController(FakeTarget(accepts=False))

    img = controller.display_screen({})

    assert isinstance(img, Image.Image)


def test_the_pairing_screen_replaces_the_picture_so_the_next_frame_is_repainted_in_full(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame())
    controller.display_pairing_screen(_pairing_panel())
    target.paints.clear()

    controller.display_screen(frame())  # identical to what was shown before the pairing screen

    assert target.paints == [FULL]


def test_with_nothing_to_show_it_says_so_instead_of_a_blank_screen(clock):
    target = FakeTarget()
    controller = DisplayController(target)

    img = controller.display_screen({})

    centre = img.convert("L").crop((100, 190, 700, 290))
    assert centre.getextrema()[0] < 100  # dark text pixels, not just white
    assert target.paints == [FULL]

    controller.display_screen({})  # and it doesn't keep repainting it
    assert target.paints == [FULL]


def test_the_setup_screen_is_one_full_paint_and_resets_what_is_remembered(clock):
    target = FakeTarget()
    controller = DisplayController(target)
    controller.display_screen(frame())
    target.paints.clear()

    controller.display_setup_screen(SetupPanel(["a weather location"]))
    assert target.paints == [FULL]

    target.paints.clear()
    controller.display_screen(frame())
    assert target.paints == [FULL]


def test_the_pairing_code_goes_to_the_log_when_there_is_no_screen_to_show_it(caplog):
    caplog.set_level(logging.INFO)
    controller = DisplayController(FakeTarget(accepts=False))

    controller.display_pairing_screen(_pairing_panel())

    assert "ABC123" in caplog.text


def test_the_pairing_code_is_not_logged_when_the_screen_shows_it(caplog):
    caplog.set_level(logging.INFO)
    controller = DisplayController(FakeTarget())

    controller.display_pairing_screen(_pairing_panel())

    assert "ABC123" not in caplog.text


def test_the_setup_checklist_goes_to_the_log_when_there_is_no_screen(caplog):
    caplog.set_level(logging.INFO)
    controller = DisplayController(FakeTarget(accepts=False))

    controller.display_setup_screen(SetupPanel(["a weather location", "a bus or tube stop"]))

    assert "a weather location, a bus or tube stop" in caplog.text


def test_a_whole_screen_that_found_no_screen_is_painted_once_one_appears():
    target = FakeTarget(accepts=False)
    controller = DisplayController(target)
    controller.display_pairing_screen(_pairing_panel())

    target.accepts = True
    controller.repaint_pending()
    assert target.paints == [FULL]

    controller.repaint_pending()
    assert target.paints == [FULL]


def test_the_dashboard_supersedes_a_screen_still_waiting_for_a_screen(clock):
    target = FakeTarget(accepts=False)
    controller = DisplayController(target)
    controller.display_pairing_screen(_pairing_panel())

    target.accepts = True
    controller.display_screen(frame())
    target.paints.clear()
    controller.repaint_pending()

    assert target.paints == []


def test_a_whole_screen_paint_that_failed_midway_is_retried():
    target = FakeTarget()
    controller = DisplayController(target)
    target.fail = True
    with pytest.raises(RuntimeError):
        controller.display_setup_screen(SetupPanel(["a weather location"]))

    target.fail = False
    controller.repaint_pending()

    assert target.paints == [FULL]


def test_nothing_is_pending_with_nothing_to_retry():
    target = FakeTarget()

    DisplayController(target).repaint_pending()

    assert target.paints == []


def test_shutdown_closes_the_target():
    target = FakeTarget()

    DisplayController(target).shutdown()

    assert target.closed == 1
