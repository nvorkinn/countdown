"""The renderer's client for the broker: the device's config and its pairing code."""

from typing import TYPE_CHECKING

import requests

from countdown_api.clients import ApiConfigClient, ApiError
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_credentials.device_name import device_name_from_env

if TYPE_CHECKING:
    from countdown_api.models import ConfigResponse
    from countdown_core.core.panel import Panel
    from countdown_credentials.registration import RendererRegistration


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
    """Talks to auth-broker (https://github.com/nvorkinn/auth-broker) for the device's config.

    It asks as the device `registration` registered.
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

    def _initialise(self) -> None:
        pass  # the registrar has done the work

    def get_config(self) -> ConfigResponse:
        """Fetch the config, as the contract says it is.

        A response that doesn't match it (a missing section, an interval that isn't positive) raises a pydantic
        ValidationError, which callers treat as a bad config.
        """
        try:
            return self._config_api.get(x_device_name=_device_name())
        except ApiError as error:
            # What callers already catch for a bad status (app.wait_for_config retries on RequestException).
            raise requests.HTTPError(str(error), response=error.response) from error

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
