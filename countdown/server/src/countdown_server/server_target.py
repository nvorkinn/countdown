"""The DisplayTarget that hands each frame to the broker: through the store, and with PUT /api/frame."""

import logging
from typing import TYPE_CHECKING, override

import requests
from redis.exceptions import RedisError

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.core.targets import DisplayTarget, Region, panel_bytes
from countdown_core.store import frame_record, reader

if TYPE_CHECKING:
    from PIL import Image

    from countdown_credentials.registration import RendererRegistration


logger = logging.getLogger(__name__)

# What the broker reads the pace from, in seconds (auth-broker's services/pace.py).
REFRESH_HINT_HEADER = "X-Refresh-Hint"


class ServerTarget(DisplayTarget):
    """Hands each frame to the broker, for the device's screen to fetch.

    Two ways, while the renderers move to handing frames over through Redis (docs/stateless-renderers.md, step 4):
    each picture goes into the screen's record in the store (store.frame_record, `v1:frame:<device id>`), written only
    when it changed, and each frame is PUT to the broker as before, which a broker that doesn't read the record needs.
    A broker that does serves the record when it's there and newer than what was PUT (or the same picture), and the
    PUT otherwise. Without a store (no REDIS_URL) there's only the PUT.

    Until a screen has been matched with this renderer, the broker accepts frames with a 202 and drops them.
    """

    def __init__(self, registration: RendererRegistration) -> None:
        """`registration` is the renderer's: where the broker is, the secret the frames go with, and its device."""
        self.base_url = registration.broker_url
        self.session = requests.Session()
        self.session.auth = registration.auth
        # Where the record goes: the store, as bytes; None without one.
        self._store = reader.binary_store()
        self._device_id = registration.device_id
        # How fast the providers want the screen to refresh, in seconds; None for the usual pace.
        self._pace: int | None = None
        # The last frame painted, which renew() makes sure the record still holds.
        self._painted: bytes | None = None
        # The last frame sent, and the pace it carried: to tell the broker again when the picture hasn't changed.
        self._last_frame: bytes | None = None
        self._sent_pace: int | None = None

    @override
    def set_pace(self, seconds: int | None) -> None:
        self._pace = seconds

    @override
    def renew(self) -> None:
        # The picture hasn't changed, so the record has it already, unless the record was lost meanwhile (it lapsed,
        # Redis was restarted): checking costs one small read, and puts it back.
        if self._painted is not None:
            self._record(self._painted)
        # The broker holds a pace for a few minutes after the frame that asked for it, and a picture that isn't
        # changing sends none. Sending the frame again renews it, or (with no pace) ends it.
        if self._last_frame is not None and (self._pace is not None or self._sent_pace is not None):
            self._put(self._last_frame)

    @override
    def paint(self, img: Image.Image, region: Region | None = None) -> bool:
        # Always the whole frame, even for a partial refresh: the broker keeps one full 800x480 frame
        # per device and rejects (400) anything of another length. The screen decides how to refresh.
        frame = bytes(panel_bytes(img))
        self._painted = frame
        # The record first, so the picture reaches the screen even while the broker can't take the PUT.
        self._record(frame)
        self._put(frame)
        return True

    def _record(self, frame: bytes) -> None:
        """Write `frame` to the screen's record in the store, unless the record has it already. Never raises.

        A Redis that can't be asked is logged and left: the PUT still reaches the broker, and the next frame (or
        renew) tries again. Nothing logged says what the frame shows.
        """
        if self._store is None:
            return
        try:
            frame_record.write_if_changed(self._store, self._device_id, frame)
        except RedisError as e:
            logger.warning("Couldn't write the frame to the store (%s)", type(e).__name__)

    def _put(self, frame: bytes) -> None:
        """Send the broker `frame`, with the pace the providers ask for (the broker's `Retry-After` follows it)."""
        headers = {} if self._pace is None else {REFRESH_HINT_HEADER: str(self._pace)}
        response = self.session.put(
            url=f"{self.base_url}/api/frame", timeout=DEFAULT_TIMEOUT, data=frame, headers=headers
        )
        response.raise_for_status()
        self._last_frame, self._sent_pace = frame, self._pace
