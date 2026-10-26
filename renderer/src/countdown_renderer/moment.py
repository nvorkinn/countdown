"""The moment a render draws the screen at: the one "now" every panel goes by while it runs.

render.render(device_id, now) draws everything as of its `now`: the page each rotating panel shows (core.rotation),
the minutes to each departure, the energy panel's windows, which notices are still in force, a release's running time
and whose turn the Spotify area is. So the same inputs draw the same frame, whenever and wherever it runs. Outside a
render (a provider, a test drawing one panel) it's simply the time.

A context variable, not a global: it's set for the render's own call and put back after it, so nothing of it is kept
between renders.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

_drawing_at: ContextVar[datetime | None] = ContextVar("drawing_at", default=None)


def now() -> datetime:
    """Return the moment being drawn (aware, in UTC): the render's `now` during one, otherwise the time."""
    moment = _drawing_at.get()
    return moment if moment is not None else datetime.now(UTC)


@contextmanager
def drawing_at(moment: datetime) -> Generator[None]:
    """Make `moment` (aware) what now() gives until the block ends."""
    if moment.tzinfo is None:
        msg = "The moment to draw at must be aware"
        raise ValueError(msg)
    token = _drawing_at.set(moment.astimezone(UTC))
    try:
        yield
    finally:
        _drawing_at.reset(token)
