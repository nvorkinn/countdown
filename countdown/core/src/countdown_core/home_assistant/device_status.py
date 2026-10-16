from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

STAGES = ("waiting_for_broker", "pairing", "running")


@dataclass
class DeviceStatus:
    """What the device is doing, for the health message to Home Assistant."""

    stage: str = "waiting_for_broker"
    last_broker_sync: datetime | None = None
    display: object | None = None  # anything with a `panel_connected` (the DisplayController)

    @property
    def display_connected(self) -> bool:
        return getattr(self.display, "panel_connected", None) is True
