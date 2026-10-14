import asyncio
import logging
import time
from datetime import UTC, datetime

import pydantic
import requests

from countdown_api.models import ConfigResponse
from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.core.api_registry import API_NAMES, ApiRegistry
from countdown_core.core.display import DisplayController
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel

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
    """The refresh loop: each cycle shows the pairing screen or the dashboard, then
    re-reads the config from the broker."""

    def __init__(
        self,
        broker: BrokerClient,
        config: ConfigResponse,
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
        self._published_stage: str | None = None

    async def run(self) -> None:
        started = time.monotonic()
        await self.api_reg.on_config_update(self.config)
        self._fit_stops_to_layout()
        first_cycle_done = False
        while True:
            try:
                if self.pairing_code_panel.pairing_code:
                    self.api_reg.status.stage = "pairing"
                    # Only repaint when the code changes: it's a full e-paper refresh, and an
                    # unpaired device can sit here for days.
                    if self.pairing_code_panel.has_changed:
                        self.display.display_pairing_screen(self.pairing_code_panel, self.config.screen)
                else:
                    self.api_reg.status.stage = "running"
                    panels = await self.api_reg.update_all()
                    self.display.display_screen(panels, self.config.screen, self.api_reg.available())
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
        self._fit_stops_to_layout()

    def _fit_stops_to_layout(self) -> None:
        """The config decides the layout, and the layout decides how many stops are on screen
        at once. Call after every on_config_update(), since that may have rebuilt the TfL client."""
        self.api_reg.show_stops(self.display.stops_shown(self.config.screen, self.api_reg.available()))
