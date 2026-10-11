import asyncio
import logging
import time
from datetime import UTC, datetime

import pydantic
import requests

from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.config_server.models import (
    AppConfig,
)
from countdown_core.core.api_registry import API_NAMES, ApiRegistry
from countdown_core.core.display import DisplayController
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_core.system_screens.setup_panel import SetupPanel

logger = logging.getLogger(__name__)


def safe_fetch(func, fallback):
    try:
        return func()
    except requests.exceptions.RequestException as e:
        logger.exception(f"Exception with API call to Glowmarkt: {e}")
        return fallback
    except pydantic.ValidationError as e:
        logger.exception(f"Pydantic validation error: {e}")
        return fallback


class DisplayLoop:
    """The refresh loop: each cycle shows the pairing screen, the setup checklist or the
    dashboard, then re-reads the config from the broker."""

    def __init__(
        self,
        broker: BrokerClient,
        config: AppConfig,
        pairing_code_panel: PairingCodePanel,
        display: DisplayController,
        api_reg: ApiRegistry | None = None,
    ):
        self.broker = broker
        self.config = config
        self.pairing_code_panel = pairing_code_panel
        self.interval = config.interval
        self.display = display
        # Without a registry of its own, one whose publisher reports nowhere.
        self.api_reg = api_reg if api_reg is not None else ApiRegistry(broker.registration, OtlpPublisher(API_NAMES))
        self._setup_shown: list[str] | None = None
        self._published_stage: str | None = None

    async def run(self) -> None:
        started = time.monotonic()
        await self.api_reg.on_config_update(self.config)
        self._apply_layout()
        first_cycle_done = False
        while True:
            try:
                if self.pairing_code_panel.pairing_code:
                    self.api_reg.status.stage = "pairing"
                    self._setup_shown = None
                    # Only repaint when the code changes: it's a full e-paper refresh, and an
                    # unpaired device can sit here for days.
                    if self.pairing_code_panel.has_changed:
                        self.display.display_pairing_screen(self.pairing_code_panel)
                elif self.config.setup_missing:
                    self.api_reg.status.stage = "setup"
                    if self.config.setup_missing != self._setup_shown:
                        self.display.display_setup_screen(SetupPanel(self.config.setup_missing))
                        self._setup_shown = list(self.config.setup_missing)
                else:
                    self.api_reg.status.stage = "running"
                    self._setup_shown = None
                    panels = await self.api_reg.update_all()
                    self.display.display_screen(panels)
                self.display.repaint_pending()
            except requests.exceptions.RequestException as e:
                logger.exception(f"Network error encountered: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error: {e}")

            # The publisher reports every minute by itself; a new stage is worth reporting at once.
            stage = self.api_reg.status.stage
            if stage != self._published_stage:
                self.api_reg.pub.publish_soon()
                self._published_stage = stage

            if not first_cycle_done:
                # Logged once, to spot a slow start without spamming the journal.
                logger.info(f"First cycle finished {time.monotonic() - started:.1f}s after start")
                first_cycle_done = True

            await asyncio.sleep(self.interval)
            await self.refresh_broker_config()

            if not self.interval:
                break

    async def refresh_broker_config(self) -> None:
        """Re-reads the config every cycle (the broker has no change-timestamp), which also
        keeps the pairing code current."""
        fetched = safe_fetch(lambda: self.broker.get_config(), None)
        if fetched is None:
            return

        self.pairing_code_panel = self.broker.get_pairing_code_panel(fetched)
        self.api_reg.status.last_broker_sync = datetime.now(UTC)
        self.config = fetched
        self.interval = fetched.interval
        await self.api_reg.on_config_update(fetched)
        self._apply_layout()

    def _apply_layout(self) -> None:
        """The config decides the layout (from which APIs are available), and the layout
        decides how many stops are on screen at once."""
        layout = self.display.use_layout(self.api_reg.available())
        self.api_reg.show_stops(layout.stops_shown)
