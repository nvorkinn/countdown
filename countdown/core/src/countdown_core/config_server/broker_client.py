import requests

from countdown_core.config_server.broker_api.clients import ApiConfigClient, ApiError
from countdown_core.config_server.models import AppConfig
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.panel import Panel
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_credentials.device_name import device_name_from_env
from countdown_credentials.registration import RendererRegistration


def _device_name() -> str | None:
    """X-Device-Name is cosmetic on the broker side, so it's sent on every call (a rename
    shows on the next poll) but never allowed to fail a request."""
    try:
        return device_name_from_env()
    except ValueError:
        return None


class BrokerClient(AbstractClient):
    """Talks to auth-broker (https://github.com/nvorkinn/auth-broker) for the device's config,
    as the device `registration` registered."""

    def __init__(self, registration: RendererRegistration):
        super().__init__(registration)
        self.device_id = registration.device_id
        self.base_url = registration.broker_url
        self.session.auth = registration.auth
        # broker_api/ is built from the countdown API contract and unpacked by core/contract/fetch.py; the session
        # brings the device's secret (session.auth) and the retries.
        self._config_api = ApiConfigClient(self.base_url, self.session, timeout=DEFAULT_TIMEOUT)

    def _initialise(self) -> None:
        pass  # the registrar has done the work

    def get_config(self) -> AppConfig:
        try:
            wire_config = self._config_api.get(x_device_name=_device_name())
        except ApiError as error:
            # What callers already catch for a bad status (app.wait_for_config retries on RequestException).
            raise requests.HTTPError(str(error), response=error.response) from error
        # The generated model is the contract; AppConfig is what the app reads of it.
        return AppConfig.model_validate(wire_config.model_dump(mode="json"))

    def get_pairing_code_panel(self, config: AppConfig) -> PairingCodePanel:
        """Wraps the config's pairing_code as a panel, flagging whether it changed since
        the last check."""
        is_same = self._cache_and_compare("pairing_code", config.pairing_code)
        return PairingCodePanel(config.pairing_code, self.device_id, has_changed=not is_same)

    def _update(self) -> Panel | None:
        pass

    def fetch_app_config(self) -> tuple[AppConfig, PairingCodePanel]:
        """The config and pairing status in one go, for boot. Raises rather than falling
        back if the broker can't be reached or the config is invalid."""
        config = self.get_config()
        return config, self.get_pairing_code_panel(config)
