from typing import override

import requests
from PIL import Image

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.core.targets import DisplayTarget, Region, panel_bytes
from countdown_credentials.registration import RendererRegistration


class ServerTarget(DisplayTarget):
    """Hands each frame to the broker, for the device's screen to fetch. Until a
    screen has been matched with this renderer, the broker accepts frames with a 202 and drops them."""

    def __init__(self, registration: RendererRegistration):
        self.base_url = registration.broker_url
        self.session = requests.Session()
        self.session.auth = registration.auth

    @override
    def paint(self, img: Image.Image, region: Region | None = None) -> bool:
        # Always the whole frame, even for a partial refresh: the broker keeps one full 800x480 frame
        # per device and rejects (400) anything of another length. The screen decides how to refresh.
        bytes_ = panel_bytes(img)
        response = self.session.put(url=f"{self.base_url}/api/frame", timeout=DEFAULT_TIMEOUT, data=bytes_)
        response.raise_for_status()
        return True
