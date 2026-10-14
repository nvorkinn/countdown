import logging
import time

from PIL import Image, ImageChops

from countdown_core.core.targets import DisplayTarget
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.screen import Canvas
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel

logger = logging.getLogger(__name__)

# Partial refreshes leave a little ghosting, so once this long has passed since the last
# full refresh the next change is repainted in full.
FULL_REFRESH_INTERVAL_S = 600


class DisplayController:
    """Gets finished frames onto the screen: what's on it, and how to refresh it. Which layout
    drew a frame is none of its business."""

    def __init__(self, target: DisplayTarget):
        self.target = target
        # What's on the screen right now, or None if that isn't known (nothing painted
        # yet, or the last paint failed).
        self._shown: Image.Image | None = None
        self._last_full_refresh = 0.0
        # A whole-screen picture (the pairing code) the screen couldn't take yet.
        self._pending: Image.Image | None = None

    @property
    def panel_connected(self) -> bool | None:
        """None until the target has been asked (or if it can't tell, like a preview)."""
        return self.target.connected

    def show(self, frame: Frame) -> None:
        """Paints `frame` only if the picture changed: a partial refresh if only its volatile
        part changed, otherwise a full one."""
        img = frame.image
        content = img.convert("RGB")
        changed = ImageChops.difference(self._shown, content).getbbox() if self._shown is not None else None
        if self._shown is not None and changed is None:
            return  # this exact picture is already on the panel

        self._pending = None  # the dashboard supersedes any screen still waiting for the panel
        region = self._partial_region(changed, frame.volatile)
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
    def _partial_region(changed, volatile) -> tuple[int, int, int, int] | None:
        """The byte-aligned box to partially refresh, or None if something outside the
        volatile part changed (or there's nothing to compare with)."""
        if changed is None or volatile is None:
            return None
        ax0, ay0, ax1, ay1 = volatile
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

    def display_pairing_screen(self, panel: PairingCodePanel, canvas: Canvas) -> Image.Image:
        img = panel.render(*canvas.size)
        if not self._paint_whole_screen(img):
            # Once per code. Without it a Pi with no screen can't be paired at all.
            logger.warning(
                f"No display to show the pairing code on -- it is {panel.pairing_code} (device {panel.device_id})"
            )
        return img

    def shutdown(self) -> None:
        # Runs inside the SIGTERM handler; targets' close() never raises.
        self.target.close()
