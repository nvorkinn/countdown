from PIL import Image

from countdown_core.core.targets import DisplayTarget, Region


class FakeTarget(DisplayTarget):
    """Records each paint -- the region for a partial refresh, None for a full one."""

    def __init__(self, accepts: bool = True):
        self.accepts = accepts  # False: there's no screen to take the picture
        self.fail = False  # True: painting raises, as a screen dying midway would
        self.paints: list[Region | None] = []
        self.closed = 0

    def paint(self, img: Image.Image, region: Region | None = None) -> bool:
        if self.fail:
            raise RuntimeError("the screen failed midway")
        if not self.accepts:
            return False
        self.paints.append(region)
        return True

    def close(self) -> None:
        self.closed += 1
