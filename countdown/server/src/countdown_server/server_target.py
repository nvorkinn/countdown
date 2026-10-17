"""The DisplayTarget that hands each frame to the broker."""

from typing import TYPE_CHECKING, override

import requests

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.core.targets import DisplayTarget, Region, panel_bytes

if TYPE_CHECKING:
    from PIL import Image

    from countdown_credentials.registration import RendererRegistration


# What the broker reads the pace from, in seconds (auth-broker's services/pace.py).
REFRESH_HINT_HEADER = "X-Refresh-Hint"


class ServerTarget(DisplayTarget):
    """Hands each frame to the broker, for the device's screen to fetch.

    Until a screen has been matched with this renderer, the broker accepts frames with a 202 and drops them.
    """

    def __init__(self, registration: RendererRegistration) -> None:
        """`registration` is the renderer's: where the broker is, and the secret it sends the frames with."""
        self.base_url = registration.broker_url
        self.session = requests.Session()
        self.session.auth = registration.auth
        # How fast the providers want the screen to refresh, in seconds; None for the usual pace.
        self._pace: int | None = None
        # The last frame sent, and the pace it carried: to tell the broker again when the picture hasn't changed.
        self._last_frame: bytes | None = None
        self._sent_pace: int | None = None

    @override
    def set_pace(self, seconds: int | None) -> None:
        self._pace = seconds

    @override
    def renew(self) -> None:
        # The broker holds a pace for a few minutes after the frame that asked for it, and a picture that isn't
        # changing sends none. Sending the frame again renews it, or (with no pace) ends it.
        if self._last_frame is not None and (self._pace is not None or self._sent_pace is not None):
            self._put(self._last_frame)

    @override
    def paint(self, img: Image.Image, region: Region | None = None) -> bool:
        # Always the whole frame, even for a partial refresh: the broker keeps one full 800x480 frame
        # per device and rejects (400) anything of another length. The screen decides how to refresh.
        self._put(bytes(panel_bytes(img)))
        return True

    def _put(self, frame: bytes) -> None:
        """Send the broker `frame`, with the pace the providers ask for (the broker's `Retry-After` follows it)."""
        headers = {} if self._pace is None else {REFRESH_HINT_HEADER: str(self._pace)}
        response = self.session.put(
            url=f"{self.base_url}/api/frame", timeout=DEFAULT_TIMEOUT, data=frame, headers=headers
        )
        response.raise_for_status()
        self._last_frame, self._sent_pace = frame, self._pace
