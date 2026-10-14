from countdown_core.config_server.models import AppConfig
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.panel import Panel
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_credentials.device_name import device_name_from_env
from countdown_credentials.registration import RendererRegistration


def _device_name_header() -> dict[str, str]:
    """X-Device-Name is cosmetic on the broker side, so it's sent on every call (a rename
    shows on the next poll) but never allowed to fail a request."""
    try:
        return {"X-Device-Name": device_name_from_env()}
    except ValueError:
        return {}


class BrokerClient(AbstractClient):
    """Talks to auth-broker (https://github.com/nvorkinn/auth-broker) for the device's config,
    as the device `registration` registered."""

    def __init__(self, registration: RendererRegistration):
        super().__init__(registration)
        self.device_id = registration.device_id
        self.base_url = registration.broker_url
        self.session.auth = registration.auth

    def _initialise(self) -> None:
        pass  # the registrar has done the work

    def _request(self, method: str, path: str, **kwargs):
        headers = _device_name_header()
        response = self.session.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=DEFAULT_TIMEOUT, **kwargs
        )
        response.raise_for_status()
        return response.json()

    def get_config(self) -> AppConfig:
        json_data = self._request("GET", "/api/config")
        return AppConfig.model_validate(json_data)

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
