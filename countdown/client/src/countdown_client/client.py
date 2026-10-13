import asyncio
import logging
import os

import requests

from countdown_credentials.registration import DEFAULT_RETRY_S, REQUEST_TIMEOUT_S, DisplayRegistrar

logger = logging.getLogger(__name__)


class Client:
    def __init__(self, registrar: DisplayRegistrar | None = None):
        self.registrar = registrar or DisplayRegistrar(os.environ["BROKER_URL"])
        self.broker_url = self.registrar.broker_url
        self.session = requests.Session()
        self._load_epd()

    def _load_epd(self):
        from countdown_epd import epd7in5_V2

        self.epd = epd7in5_V2.EPD()

    async def start(self) -> None:
        while True:
            registration = await self.registrar.register()
            self.session.auth = registration.auth
            while True:
                try:
                    response = self.session.get(
                        url=f"{self.broker_url}/api/frame",
                        json={"role": "display"},
                        timeout=REQUEST_TIMEOUT_S,
                    )
                except requests.RequestException as e:
                    logger.warning("Couldn't fetch the frame: %s", e)
                    await asyncio.sleep(DEFAULT_RETRY_S)
                    continue
                next_poll = int(response.headers.get("Retry-After", DEFAULT_RETRY_S))
                match response.status_code:
                    case 200:
                        frame = bytearray(response.content)
                        self._display_frame(frame)
                    case 202:
                        pass  # Not matched with a renderer yet
                    case 304:
                        pass  # Nothing has changed since last poll
                    case 401:
                        break  # The broker doesn't know the secret (unlinked)
                    case 404:
                        pass  # Matched, but nothing rendered yet
                    case _:
                        logger.warning("Unexpected %s from %s", response.status_code, response.url)
                await asyncio.sleep(next_poll)

    def _display_frame(self, frame: bytearray) -> None:
        self.epd.init()
        self.epd.display(frame)
        self.epd.sleep()
