"""Registering with auth-broker (https://github.com/nvorkinn/auth-broker), and the device credentials
that come out of it. A Registrar does the registering, and is the one place that reads and writes
CREDENTIALS_FILE. What it hands back, a Registration, never changes: everything else talks to the
broker through a session whose `auth` is a Registration's `auth`, so the bearer secret is set in one place.

Until it's registered, a client has nothing to do but wait, so register() only returns once it is.
"""

import asyncio
import json
import logging
import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, override

import requests
from cryptography.fernet import Fernet, InvalidToken

from countdown_credentials.credentials_key import credentials_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = Path(".auth_broker_device")
# How long to wait when the broker doesn't say (no Retry-After) or can't be reached.
DEFAULT_RETRY_S = 60
# So a stalled connection raises (and is retried) instead of hanging the wait.
REQUEST_TIMEOUT_S = 10


def _bearer(request: requests.PreparedRequest, secret: str) -> requests.PreparedRequest:
    request.headers["Authorization"] = f"Bearer {secret}"
    return request


def _owner_only(path: str, flags: int) -> int:
    """An opener for open(): the file is created readable by its owner only, never briefly by anyone else."""
    return os.open(path, flags, 0o600)


def _retry_after(response: requests.Response) -> int:
    return int(response.headers.get("Retry-After", DEFAULT_RETRY_S))


@dataclass(frozen=True)
class Registration:
    """A device the broker knows: where the broker is, and the secret it knows the device by."""

    broker_url: str
    device_secret: str

    def auth(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        """For any session's `auth`: sends the device secret as a bearer token."""
        return _bearer(request, self.device_secret)


@dataclass(frozen=True)
class RendererRegistration(Registration):
    """A renderer's registration, which also carries the device it was matched into."""

    device_id: str


class Registrar[R: Registration](ABC):
    """Registers the device as one role, and keeps its credentials in `credentials_file`. A new secret
    is saved before it's ever sent, so retrying a registration never makes a second identity.
    """

    role: str
    path = "/api/devices/register"

    def __init__(
        self,
        broker_url: str,
        credentials_file: Path = CREDENTIALS_FILE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        cipher: Fernet | None = None,
    ):
        self.broker_url = broker_url.rstrip("/")
        self.credentials_file = credentials_file
        self._cipher = cipher or credentials_key()
        self._sleep = sleep
        self._session = requests.Session()
        self._session.auth = self._auth
        self._device_id: str | None = None
        self._device_secret = ""
        self._load()
        if not self._device_secret:
            self._new_secret()

    async def register(self) -> R:
        """The registration, once the broker has taken it, retrying until it does."""
        if (cached := self._cached()) is not None:
            return cached
        while True:
            try:
                # In a thread, so a slow broker doesn't hold up the rest of the event loop.
                response = await asyncio.to_thread(
                    self._session.post,
                    f"{self.broker_url}{self.path}",
                    json=self._register_body(),
                    timeout=REQUEST_TIMEOUT_S,
                )
            except requests.RequestException as e:
                logger.warning("Couldn't register with the broker: %s", e)
                await self._sleep(DEFAULT_RETRY_S)
                continue
            match response.status_code:
                case 200 | 201:
                    return self._registered(response)
                case 202:
                    return self._accepted()
                case 429 | 503:
                    delay = _retry_after(response)
                case 409:
                    logger.warning("The secret is registered with the other role; registering a new one")
                    self._new_secret()
                    continue
                case 400 | 403:
                    logger.error("The broker rejected the registration: %s", response.text)
                    delay = DEFAULT_RETRY_S
                case _:
                    logger.warning("Unexpected %s from %s", response.status_code, response.url)
                    delay = DEFAULT_RETRY_S
            await self._sleep(delay)

    def _cached(self) -> R | None:
        """A registration from the credentials file that needs no call to the broker."""
        return None

    @abstractmethod
    def _register_body(self) -> dict[str, object]:
        pass

    @abstractmethod
    def _registered(self, response: requests.Response) -> R:
        """The registration, from /register's 200 or 201."""

    def _accepted(self) -> R:
        """The registration, from a 202 (only a display's /register answers that)."""
        raise NotImplementedError

    def _auth(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        return _bearer(request, self._device_secret)

    def _load(self) -> None:
        if not self.credentials_file.exists():
            return
        raw = self.credentials_file.read_bytes()
        try:
            data = json.loads(self._cipher.decrypt(raw))
        except InvalidToken as e:
            # Not starting a new identity over an unreadable file: that would orphan the device on the broker.
            msg = f"{self.credentials_file} wasn't encrypted with this device's credentials key"
            raise RuntimeError(msg) from e
        self._device_secret = data.get("device_secret", "")
        self._device_id = data.get("device_id")

    def _save(self) -> None:
        data = {"device_secret": self._device_secret}
        if self._device_id is not None:
            data["device_id"] = self._device_id
        encrypted = self._cipher.encrypt(json.dumps(data).encode())
        # The secret is the device's identity, so only the app's own user may read it.
        with open(self.credentials_file, "wb", opener=_owner_only) as file:
            file.write(encrypted)

    def _new_secret(self) -> None:
        self._device_secret = secrets.token_urlsafe(24)
        self._device_id = None  # the old id belonged to the old secret
        self._save()


class RendererRegistrar(Registrar[RendererRegistration]):
    """Whatever draws the frames. The orchestrator starts it for one screen and gives it that screen's
    claim token, which it sends with its secret to POST /api/renderers/claim. The broker answers 201 with
    the device_id on the first claim, and 200 on a repeat with the same secret (the token is then ignored).
    """

    role = "renderer"
    path = "/api/renderers/claim"

    def __init__(self, broker_url: str, claim_token: str, **kwargs):
        if not claim_token:
            msg = "A renderer can only register with a claim token"
            raise ValueError(msg)
        self.claim_token = claim_token
        super().__init__(broker_url, **kwargs)

    @override
    def _cached(self) -> RendererRegistration | None:
        if self._device_id is None:
            return None
        return RendererRegistration(self.broker_url, self._device_secret, self._device_id)

    @override
    def _register_body(self) -> dict[str, object]:
        return {"secret": self._device_secret, "claim_token": self.claim_token}

    @override
    def _registered(self, response: requests.Response) -> RendererRegistration:
        return self._claimed(response.json()["device_id"])

    def _claimed(self, device_id: str) -> RendererRegistration:
        self._device_id = device_id
        self._save()
        logger.info("Registered as device %s", device_id)
        return RendererRegistration(self.broker_url, self._device_secret, device_id)


class DisplayRegistrar(Registrar[Registration]):
    """A thin screen that only fetches finished frames. Registers on every boot (the broker allows
    it) and doesn't wait to be matched: until it is, its frame polls answer 202. It never needs
    its device_id.
    """

    role = "display"

    @override
    def _register_body(self) -> dict[str, object]:
        return {"role": self.role, "secret": self._device_secret}

    @override
    def _registered(self, response: requests.Response) -> Registration:
        return Registration(self.broker_url, self._device_secret)

    @override
    def _accepted(self) -> Registration:
        return Registration(self.broker_url, self._device_secret)
