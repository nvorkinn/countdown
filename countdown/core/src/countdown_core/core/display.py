import logging
import time

from PIL import Image, ImageChops

from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.core.targets import DisplayTarget
from countdown_core.display_composers import DEFAULT_COMPOSER, choose_composer
from countdown_core.display_composers.abstract_display_composer import AbstractDisplayComposer
from countdown_core.system_screens.empty_panel import EmptyPanel
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_core.utils.utils import TOTAL_HEIGHT, TOTAL_WIDTH

logger = logging.getLogger(__name__)

# Partial refreshes leave a little ghosting, so once this long has passed since the last
# full refresh the next change is repainted in full.
FULL_REFRESH_INTERVAL_S = 600


class DisplayController:
    def __init__(self, target: DisplayTarget):
        self.target = target
        # What's on the screen right now, or None if that isn't known (nothing painted
        # yet, or the last paint failed).
        self._shown: Image.Image | None = None
        self._last_full_refresh = 0.0
        # A whole-screen picture (the pairing code) the screen couldn't take yet.
        self._pending: Image.Image | None = None
        self._composer = DEFAULT_COMPOSER

    @property
    def panel_connected(self) -> bool | None:
        """None until the target has been asked (or if it can't tell, like a preview)."""
        return self.target.connected

    def use_layout(self, available: frozenset[ClientClasses]) -> AbstractDisplayComposer:
        """Picks the layout for a device that can show `available`, and returns it."""
        composer = choose_composer(available)
        if type(composer) is not type(self._composer):
            logger.info(f"Changing layout from: {type(self._composer).__name__} to {type(composer).__name__}")
        self._composer = composer
        return composer

    def display_screen(self, panels: dict[str, Panel]) -> Image.Image:
        """Composes the panels and paints them only if the picture changed: a partial
        refresh if only the arrivals changed, otherwise a full one. If every panel is a
        MessagePanel, shows the "nothing to show yet" screen instead."""
        if all(isinstance(panel, MessagePanel) for panel in panels.values()):
            img, arrivals_box = EmptyPanel().render(TOTAL_WIDTH, TOTAL_HEIGHT), None
        else:
            img, arrivals_box = self._composer.compose(panels)
        content = img.convert("RGB")
        self._show(img, content, arrivals_box)
        return img

    def _show(self, img: Image.Image, content: Image.Image, arrivals_box) -> None:
        changed = ImageChops.difference(self._shown, content).getbbox() if self._shown is not None else None
        if self._shown is not None and changed is None:
            return  # this exact picture is already on the panel

        self._pending = None  # the dashboard supersedes any screen still waiting for the panel
        region = self._partial_region(changed, arrivals_box)
        now = time.monotonic()
        try:
            if region is not None and now - self._last_full_refresh < FULL_REFRESH_INTERVAL_S:
                painted = self.target.paint(img, region)
            else:
                painted = self.target.paint(img)
                if painted:
                    self._last_full_refresh = now
        except Exception:
            self._shown = None  # can't know what state the panel was left in
            raise
        self._shown = content if painted else None

    @staticmethod
    def _partial_region(changed, arrivals_box) -> tuple[int, int, int, int] | None:
        """The byte-aligned box to partially refresh, or None if something outside the
        arrivals changed (or there's nothing to compare with)."""
        if changed is None or arrivals_box is None:
            return None
        ax0, ay0, ax1, ay1 = arrivals_box
        if changed[0] < ax0 or changed[1] < ay0 or changed[2] > ax1 or changed[3] > ay1:
            return None
        return 0, ay0, -(-ax1 // 8) * 8, ay1  # x in whole bytes (8 pixels), as the panel needs

    def _paint_whole_screen(self, img: Image.Image) -> bool:
        """For the screens that replace the picture outright (pairing code, checklist). False
        if there's no screen to paint on."""
        # So the next dashboard is painted in full even if it matches the last one.
        self._shown = None
        self._pending = img
        if not self.target.paint(img):
            return False
        self._pending = None
        return True

    def repaint_pending(self) -> None:
        """Retries a whole-screen picture that couldn't be painted, so it appears once the
        panel does."""
        if self._pending is not None:
            self._paint_whole_screen(self._pending)

    def display_pairing_screen(self, panel: PairingCodePanel) -> Image.Image:
        img = panel.render(TOTAL_WIDTH, TOTAL_HEIGHT)
        if not self._paint_whole_screen(img):
            # Once per code. Without it a Pi with no screen can't be paired at all.
            logger.warning(
                f"No display to show the pairing code on -- it is {panel.pairing_code} (device {panel.device_id})"
            )
        return img

    def shutdown(self) -> None:
        # Runs inside the SIGTERM handler; targets' close() never raises.
        self.target.close()
