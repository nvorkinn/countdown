from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageChops

from countdown_shared.store import keys
from countdown_shared.store.models import Departure, StopDepartures

if TYPE_CHECKING:
    from collections.abc import Sequence

# The screen a client is built for, whose keys by device it reads.
TEST_DEVICE_ID = "device-123"


def put_departures(  # noqa: PLR0913 -- a stop as a provider stores it, every field optional
    store,
    stop_id: str,
    departures: Sequence[tuple[str, str, int]] = (),
    *,
    name: str = "Elephant & Castle",
    mode: str = "bus",
    stop_letter: str | None = None,
    now: datetime | None = None,
) -> StopDepartures:
    """Store a stop's departures as its provider would: each (line, destination, seconds to go from `now`)."""
    now = now or datetime.now(UTC)
    stored = StopDepartures(
        fetched_at=now,
        stop_id=stop_id,
        name=name,
        mode=mode,
        stop_letter=stop_letter,
        departures=[
            Departure(line=line, destination=to, expected_at=now + timedelta(seconds=seconds))
            for line, to, seconds in departures
        ],
    )
    store.set(keys.departures(stop_id), stored.model_dump_json())
    return stored


def images_equal(img1: Image.Image, img2: Image.Image, threshold: float = 0.0) -> bool:
    """Check whether two images are pixel-identical, or within `threshold` mean normalized difference.

    The default, threshold=0.0, means pixel-identical.
    """
    if img1.size != img2.size or img1.mode != img2.mode:
        return False
    diff = ImageChops.difference(img1.convert("RGB"), img2.convert("RGB"))
    if threshold == 0.0:
        return diff.getbbox() is None
    return (np.array(diff).mean() / 255.0) <= threshold


def highlight_diff(expected: Image.Image, actual: Image.Image) -> Image.Image:
    """Return `actual` with differing pixels painted red, for quick visual review of a snapshot mismatch.

    Assumes expected/actual are already the same size.
    """
    diff = ImageChops.difference(expected.convert("RGB"), actual.convert("RGB"))
    mask = diff.convert("L").point(lambda p: 255 if p > 10 else 0)
    highlight = Image.new("RGB", actual.size, (255, 0, 0))
    return Image.composite(highlight, actual.convert("RGB"), mask)


class StopWaitingError(Exception):
    """Raised by Sleeps to break out of a loop that would otherwise wait forever."""


class Sleeps(list):
    """Stands in for asyncio.sleep: records each wait, and raises StopWaitingError at the `stop_after`th.

    So a test can end a retry loop after as many waits as it means to see.
    """

    def __init__(self, stop_after: int = 50):
        super().__init__()
        self.stop_after = stop_after

    async def __call__(self, seconds: float) -> None:
        self.append(seconds)
        if len(self) >= self.stop_after:
            raise StopWaitingError


def assert_text_inside_and_apart(drawn: list, size: tuple[int, int]) -> None:
    """Check every text the `drawn_text` fixture recorded is inside an image `size` big, and none on top of another.

    An empty text (a header without an aside) draws nothing, and doesn't count.
    """
    width, height = size
    drawn = [call for call in drawn if call[0]]
    for text, (left, top, right, bottom), _ in drawn:
        assert 0 <= left < right <= width, text
        assert 0 <= top < bottom <= height, text
    for first, second in combinations(drawn, 2):
        _, (left_a, top_a, right_a, bottom_a), _ = first
        _, (left_b, top_b, right_b, bottom_b), _ = second
        assert right_a <= left_b or right_b <= left_a or bottom_a <= top_b or bottom_b <= top_a, (first, second)
