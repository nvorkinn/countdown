from typing import TYPE_CHECKING

from countdown_core.core.targets import DisplayTarget, Region

if TYPE_CHECKING:
    from PIL import Image


class FakeTarget(DisplayTarget):
    """Records each paint -- the region for a partial refresh, None for a full one."""

    def __init__(self, *, accepts: bool = True) -> None:
        self.accepts = accepts  # False: there's no screen to take the picture
        self.fail = False  # True: painting raises, as a screen dying midway would
        self.paints: list[Region | None] = []
        self.closed = 0
        self.paces: list[int | None] = []  # each set_pace()
        self.renewals = 0

    def set_pace(self, seconds: int | None) -> None:
        self.paces.append(seconds)

    def renew(self) -> None:
        self.renewals += 1

    def paint(self, img: Image.Image, region: Region | None = None) -> bool:
        if self.fail:
            msg = "the screen failed midway"
            raise RuntimeError(msg)
        if not self.accepts:
            return False
        self.paints.append(region)
        return True

    def close(self) -> None:
        self.closed += 1
