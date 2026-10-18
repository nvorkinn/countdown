"""Getting each frame the render drew onto the screen, with a partial refresh where that's enough.

render.render draws the frame; this only decides whether and how to paint it, from the picture last painted.
"""

import logging
import time
from typing import TYPE_CHECKING

from PIL import Image, ImageChops

if TYPE_CHECKING:
    from countdown_core.core.targets import DisplayTarget
    from countdown_core.rendering.layout import Box, Frame

logger = logging.getLogger(__name__)

# Partial refreshes leave a little ghosting, so once this long has passed since the last
# full refresh the next change is repainted in full.
FULL_REFRESH_INTERVAL_S = 600


class DisplayController:
    """Gets each frame onto the panel: what's kept is what's on the panel, to decide how to refresh it."""

    def __init__(self, target: DisplayTarget) -> None:
        """`target` is where the frames are painted."""
        self.target = target
        # What's on the screen right now, or None if that isn't known (nothing painted
        # yet, or the last paint failed).
        self._shown: Image.Image | None = None
        self._last_full_refresh = 0.0

    @property
    def panel_connected(self) -> bool | None:
        """None until the target has been asked (or if it can't tell, like a preview)."""
        return self.target.connected

    def set_pace(self, seconds: int | None) -> None:
        """Pass the refresh pace the providers ask for (None: the usual) on to the target, for its next frame."""
        self.target.set_pace(seconds)

    def show(self, frame: Frame) -> None:
        """Paint `frame` (as the panel takes it: render.Outcome.frame), only if the picture changed.

        A partial refresh if only its volatile part changed, otherwise a full one; the same picture again only renews
        the target's pace. A picture the target couldn't take is painted again next time.
        """
        img = frame.image
        content = img.convert("RGB")
        changed = ImageChops.difference(self._shown, content).getbbox() if self._shown is not None else None
        if self._shown is not None and changed is None:
            # This exact picture is already on the panel; the target only has the pace to keep telling.
            self.target.renew()
            return

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
    def _partial_region(changed: Box | None, volatile: Box | None) -> tuple[int, int, int, int] | None:
        """Return the byte-aligned box to partially refresh.

        None if something outside the volatile part changed (or there's nothing to compare with).
        """
        if changed is None or volatile is None:
            return None
        ax0, ay0, ax1, ay1 = volatile
        if changed[0] < ax0 or changed[1] < ay0 or changed[2] > ax1 or changed[3] > ay1:
            return None
        return 0, ay0, -(-ax1 // 8) * 8, ay1  # x in whole bytes (8 pixels), as the panel needs

    def shutdown(self) -> None:
        """Let the target release whatever it holds."""
        # Runs inside the SIGTERM handler; targets' close() never raises.
        self.target.close()
