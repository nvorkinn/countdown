from datetime import UTC, datetime, timedelta

import numpy as np
from PIL import Image, ImageChops

from countdown_core.store import keys
from countdown_core.store.models import Departure, StopDepartures
from countdown_credentials.registration import RendererRegistration

# A device that's already registered, for any client that needs one.
REGISTRATION = RendererRegistration("https://broker.example.com", "shh", "device-123")


def put_departures(  # noqa: PLR0913 -- a stop as a provider stores it, every field optional
    store,
    stop_id: str,
    departures: list[tuple[str, str, int]] = (),
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
