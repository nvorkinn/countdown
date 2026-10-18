"""What a screen carries from one render to the next, kept where a render that runs and exits can find it again.

Today each screen's renderer is a long-lived process, which keeps this in memory: whether the picture changed
(DisplayController._shown) and what it last sent the broker (ServerTarget._last_frame and _sent_pace). A render job
that draws one frame and exits has nowhere to keep it but a store. This module is its shape (RenderState), the rule it
serves (RenderState.needs_put, today's rule written over a hash of the frame rather than the picture), and the
interface to keep it behind (RenderStateStore), with an in-memory store. A Redis one, under
`v1:render-state:<device id>` with a TTL, comes with the step that uses it; see docs/stateless-renderers.md.

Nothing uses it yet: the renderer still decides as before.
"""

import logging
from hashlib import blake2b
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


def frame_etag(frame: bytes) -> str:
    """Return the frame's hash, as the broker makes the ETag it serves the screen the frame with.

    That's an 8-byte BLAKE2b, in hex (auth-broker's `routes/frames.py`). Two frames with the same hash are the same
    picture, so a render whose frame hashes like the last one handed off has nothing new to send.
    """
    return blake2b(frame, digest_size=8).hexdigest()


class Handoff(BaseModel):
    """The last frame the broker took for the screen: its hash, the refresh pace it carried, and when."""

    model_config = ConfigDict(frozen=True)

    etag: str
    # The X-Refresh-Hint it went with, in seconds; None: none, so the screen refreshes at its usual pace.
    pace: int | None = None
    at: AwareDatetime


class RenderState(BaseModel):
    """Everything one render of a screen leaves for the next. Empty before the first, and after it's lost."""

    model_config = ConfigDict(frozen=True)

    # Bumped for a change the last version can't read: a state that can't be read is no state (see decode).
    version: Literal[1] = 1
    # None until a frame has been handed off.
    handoff: Handoff | None = None

    def needs_put(self, etag: str, pace: int | None) -> bool:
        """Whether this render's frame, hashed `etag`, drawn while the providers ask for `pace`, goes to the broker.

        The rule the renderer follows now: a changed picture always does. The same one again only while a pace is
        asked for, which sending it renews (the broker holds a pace for a few minutes after the frame that carried
        it), or once more after the pace was dropped, which ends it at once. Otherwise the broker already has it.
        """
        last = self.handoff
        if last is None or last.etag != etag:
            return True
        return pace is not None or last.pace is not None

    def handed_off(self, etag: str, pace: int | None, at: AwareDatetime) -> RenderState:
        """Return this state after the broker took the frame hashed `etag`, with `pace`, at `at`.

        Only after it took it: a frame it refused (or never got) must be sent again, so the state stays as it was.
        """
        return self.model_copy(update={"handoff": Handoff(etag=etag, pace=pace, at=at)})


def encode(state: RenderState) -> str:
    """Return `state` as a store keeps it: JSON."""
    return state.model_dump_json()


def decode(value: str | bytes | None) -> RenderState:
    """Return the state a store kept as `value`; an empty one if there's none, or it can't be read.

    Starting again from nothing is always safe: the next frame is handed off whatever it is.
    """
    if value is None:
        return RenderState()
    try:
        return RenderState.model_validate_json(value)
    except ValidationError as e:
        logger.warning("Couldn't read a screen's render state (%s); starting from none", type(e).__name__)
        return RenderState()


class RenderStateStore(Protocol):
    """Where each screen's RenderState is kept between renders, by its device id."""

    def load(self, device_id: str) -> RenderState:
        """Return the screen's state: an empty one if there's none (never rendered, expired) or it can't be read."""
        ...

    def save(self, device_id: str, state: RenderState) -> None:
        """Keep `state` as the screen's, in place of what was kept before."""
        ...


class InMemoryRenderStateStore:
    """A RenderStateStore in this process's memory, which a renderer that keeps running can use.

    It keeps each state encoded, as a store outside the process would, so a state that couldn't make that trip fails
    here first.
    """

    def __init__(self) -> None:
        """Start with no screen's state."""
        self._values: dict[str, str] = {}

    def load(self, device_id: str) -> RenderState:
        """See RenderStateStore.load."""
        return decode(self._values.get(device_id))

    def save(self, device_id: str, state: RenderState) -> None:
        """See RenderStateStore.save."""
        self._values[device_id] = encode(state)
