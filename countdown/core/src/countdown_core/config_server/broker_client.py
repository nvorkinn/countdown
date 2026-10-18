"""The renderer's client for its config and its pairing code: from the store, else from the broker."""

import logging
from typing import TYPE_CHECKING

import pydantic
import requests
from redis.exceptions import RedisError

from countdown_api.clients import ApiConfigClient, ApiError
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.store import config as stored_config
from countdown_core.store import reader
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_credentials.device_name import device_name_from_env

if TYPE_CHECKING:
    from countdown_api.models import ConfigResponse
    from countdown_core.core.panel import Panel
    from countdown_credentials.registration import RendererRegistration

logger = logging.getLogger(__name__)


def _device_name() -> str | None:
    """Return the X-Device-Name to send, or None if there's no usable name.

    It's cosmetic on the broker side, so it's sent on every call (a rename shows on the next poll) but never allowed
    to fail a request.
    """
    try:
        return device_name_from_env()
    except ValueError:
        return None


class BrokerClient(AbstractClient):
    """The device's config, from the store or from auth-broker (https://github.com/nvorkinn/auth-broker).

    The store's is the copy the broker keeps there; the broker's is its GET /api/config, asked as the device
    `registration` registered.
    """

    def __init__(self, registration: RendererRegistration) -> None:
        """`registration` is the renderer's: where the broker is, the device's secret and its device_id."""
        super().__init__(registration)
        self.device_id = registration.device_id
        self.base_url = registration.broker_url
        self.session.auth = registration.auth
        # countdown_api is the countdown API contract's client (https://github.com/nvorkinn/countdown-api); the
        # session brings the device's secret (session.auth) and the retries.
        self._config_api = ApiConfigClient(self.base_url, self.session, timeout=DEFAULT_TIMEOUT)
        # Where the last config came from, "store" or "broker", to log only when that changes.
        self.config_source: str | None = None

    def _initialise(self) -> None:
        pass  # the registrar has done the work

    def get_config(self) -> ConfigResponse:
        """Return the config, as the contract says it is: the broker's copy in the store, or else the broker's answer.

        The store's copy (store.config) has everything but a pairing code, which only the broker gives, so the broker
        keeps none there while the device has one to show. It's also asked when there's no store (no REDIS_URL), when
        Redis can't be reached, and when what's stored doesn't validate.

        A response from the broker that doesn't match the contract (a missing section, an interval that isn't
        positive) raises a pydantic ValidationError, which callers treat as a bad config; one it can't be asked
        raises a requests exception.
        """
        stored = self._stored_config()
        if stored is not None:
            self._came_from("store")
            return stored
        fetched = self.fetch_config()
        self._came_from("broker")
        return fetched

    def fetch_config(self) -> ConfigResponse:
        """Fetch the config from the broker, GET /api/config, whatever the store has.

        A response that doesn't match the contract raises a pydantic ValidationError.
        """
        try:
            return self._config_api.get(x_device_name=_device_name())
        except ApiError as error:
            # What callers already catch for a bad status (app.wait_for_config retries on RequestException).
            raise requests.HTTPError(str(error), response=error.response) from error

    def _stored_config(self) -> ConfigResponse | None:
        """Return the config the broker keeps in the store for this device; None if there's none, or no store.

        A store that can't be read, or holds something that isn't a config, is logged and taken as none, so the
        broker is asked instead.
        """
        client = reader.store()
        if client is None:
            return None
        try:
            return stored_config.read(client, self.device_id)
        except RedisError as e:
            logger.warning("Can't read the config from the store (%s); asking the broker", type(e).__name__)
        except pydantic.ValidationError:
            logger.warning("The config in the store isn't valid for this renderer; asking the broker")
        return None

    def _came_from(self, source: str) -> None:
        """Note where the config came from, logging it when that changes."""
        if source != self.config_source:
            where = "the store" if source == "store" else "the broker (there's none for it in the store)"
            logger.info("Reading the config from %s", where)
            self.config_source = source

    def get_pairing_code_panel(self, config: ConfigResponse) -> PairingCodePanel:
        """Wrap the config's pairing_code as a panel, flagging whether it changed since the last check."""
        is_same = self._cache_and_compare("pairing_code", config.pairing_code)
        return PairingCodePanel(config.pairing_code, self.device_id, has_changed=not is_same)

    def _update(self) -> Panel | None:
        pass

    def fetch_app_config(self) -> tuple[ConfigResponse, PairingCodePanel]:
        """Fetch the config and pairing status in one go, for boot.

        Raises rather than falling back if the broker can't be reached or the config is invalid.
        """
        config = self.get_config()
        return config, self.get_pairing_code_panel(config)
