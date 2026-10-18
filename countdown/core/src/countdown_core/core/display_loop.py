"""The refresh loop: draw and paint the screen, re-read the config, and again."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pydantic
import requests

from countdown_core.core.api_registry import API_NAMES, ApiRegistry
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher

if TYPE_CHECKING:
    from collections.abc import Callable

    from countdown_api.models import ConfigResponse
    from countdown_core.config_server.broker_client import BrokerClient
    from countdown_core.core.display import DisplayController
    from countdown_core.store.config import ConfigWatcher
    from countdown_core.system_screens.pairing_code_panel import PairingCodePanel

logger = logging.getLogger(__name__)

# The fastest a screen is ever asked to refresh, in seconds, whatever a provider asks for (the broker's floor too).
MIN_PACE_S = 20


def safe_fetch[T, F](func: Callable[[], T], fallback: F) -> T | F:
    """Return `func()`, or `fallback` (logged) if it fails with a network or validation error."""
    try:
        return func()
    except requests.exceptions.RequestException:
        logger.exception("Exception with API call to Glowmarkt")
        return fallback
    except pydantic.ValidationError:
        logger.exception("Pydantic validation error")
        return fallback


class DisplayLoop:
    """The refresh loop: each cycle shows the pairing screen or the dashboard, then re-reads the config."""

    def __init__(  # noqa: PLR0913 -- what the boot hands over; the optional watcher is keyword-only
        self,
        broker: BrokerClient,
        config: ConfigResponse,
        pairing_code_panel: PairingCodePanel,
        display: DisplayController,
        api_reg: ApiRegistry | None = None,
        *,
        watcher: ConfigWatcher | None = None,
    ) -> None:
        """Start from the boot's `config` and `pairing_code_panel`, painting on `display`.

        `broker` gives the later configs; `api_reg` holds the clients (a fresh one that reports nowhere if not given);
        `watcher`, if given, cuts the wait between cycles short when the broker says the config changed.
        """
        self.broker = broker
        self.watcher = watcher
        self.config = config
        self.pairing_code_panel = pairing_code_panel
        self.interval = config.interval
        self.display = display
        # Without a registry of its own, one whose publisher reports nowhere.
        self.api_reg = api_reg if api_reg is not None else ApiRegistry(broker.registration, OtlpPublisher(API_NAMES))
        self._published_stage: str | None = None
        # How fast the providers ask for the screen to refresh as of the last cycle, in seconds; None: not at all.
        self._pace: int | None = None

    async def run(self) -> None:
        """Run the cycles, the config's interval apart (less while a provider asks for it); 0 stops after the cycle.

        A change to the config the broker announces (see `watcher`) starts the next cycle at once, with it.
        """
        if self.watcher is not None:
            self.watcher.start()
        try:
            await self._run()
        finally:
            if self.watcher is not None:
                await self.watcher.close()

    async def _run(self) -> None:
        started = time.monotonic()
        await self.api_reg.on_config_update(self.config)
        self._fit_stops_to_layout()
        first_cycle_done = False
        while True:
            try:
                if self.pairing_code_panel.pairing_code:
                    self.api_reg.status.stage = "pairing"
                    self._pace = None
                    self.display.set_pace(None)
                    # Only repaint when the code changes: it's a full e-paper refresh, and an
                    # unpaired device can sit here for days.
                    if self.pairing_code_panel.has_changed:
                        self.display.display_pairing_screen(self.pairing_code_panel, self.config.screen)
                else:
                    self.api_reg.status.stage = "running"
                    panels = await self.api_reg.update_all()
                    # After the update, which may have just seen the burst begin; before the paint, which carries it.
                    self._pace = await self.api_reg.pace()
                    self.display.set_pace(self._pace)
                    self.display.display_screen(
                        panels, self.config.screen, self.api_reg.available(), clock_format=self.config.clock_format
                    )
                self.display.repaint_pending()
            except requests.exceptions.RequestException:
                logger.exception("Network error encountered")
            except Exception:
                logger.exception("Unexpected error")

            # The publisher reports every minute by itself; a new stage is worth reporting at once.
            stage = self.api_reg.status.stage
            if stage != self._published_stage:
                self.api_reg.pub.publish_soon()
                self._published_stage = stage

            if not first_cycle_done:
                # Logged once, to spot a slow start without spamming the journal.
                logger.info("First cycle finished %.1fs after start", time.monotonic() - started)
                first_cycle_done = True

            await self._wait(self.sleep_s())
            await self.refresh_broker_config()

            if not self.interval:
                break

    async def _wait(self, seconds: float) -> None:
        """Sleep `seconds` between cycles, or less if the broker says the config changed meanwhile."""
        if self.watcher is None:
            await asyncio.sleep(seconds)
        else:
            await self.watcher.wait(seconds)

    def sleep_s(self) -> float:
        """Return how long to wait before the next cycle: the config's interval, or a provider's pace if that's shorter.

        An interval of 0 stays 0 (the cycle is the last), and no pace goes under MIN_PACE_S.
        """
        if not self.interval or self._pace is None:
            return self.interval
        return min(self.interval, max(self._pace, MIN_PACE_S))

    async def refresh_broker_config(self) -> None:
        """Re-read the config (from the store, else the broker), as every cycle does, and after the broker's nudge.

        That also keeps the pairing code current.
        """
        fetched = safe_fetch(self.broker.get_config, None)
        if fetched is None:
            return

        self.pairing_code_panel = self.broker.get_pairing_code_panel(fetched)
        self.api_reg.status.last_broker_sync = datetime.now(UTC)
        self.config = fetched
        self.interval = fetched.interval
        await self.api_reg.on_config_update(fetched)
        self._fit_stops_to_layout()

    def _fit_stops_to_layout(self) -> None:
        """Tell the departures client how many stops are on screen at once, which the config's layout decides.

        Call after every on_config_update(), since that may have rebuilt the TfL client.
        """
        self.api_reg.show_stops(self.display.stops_shown(self.config.screen, self.api_reg.available()))
