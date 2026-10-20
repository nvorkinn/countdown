"""The energy panel's readings in the store: the value of `v1:energy:<device id>`.

Written by auth-broker's glow-provider, not by countdown-providers: the Glowmarkt passwords are the broker's, and stay
there. The broker can't import countdown-core, so the value is Glowmarkt's own answers, as the broker got them, in a
thin wrapper, and they're read with the models of Glowmarkt's answers (countdown_core.glow.models). The store contract
(countdown-api's store.yaml: StoredEnergy) pins the wrapper, and both repositories test against its glow_energy.json
(countdown_api.fixtures).

    {"fetched_at": "2026-09-25T05:10:00+00:00", "device_id": "a1b2c3d4e5f6", "offset": "-60", "resource_id": "...",
     "hourly": <Glowmarkt's PT1H readings>, "daily": <P1D>, "monthly": <P1M>}

Each page's readings run from the start of its window's first bucket as of `fetched_at` (the hour 24 hours before,
midnight 31 days before, 1st January) to `fetched_at`. The renderer cuts each page to its
window as of when it draws (GlowClient._update).
"""

from datetime import timedelta, timezone

from pydantic import Field

from countdown_core.glow.models import Readings  # noqa: TC001 -- pydantic reads the annotations at runtime
from countdown_core.store.models import Stored


class StoredEnergy(Stored):
    """A Glow device's last 24 hours, 31 days and this year of electricity use, as Glowmarkt gave them."""

    device_id: str
    # Glowmarkt's `offset` parameter the readings were asked for with: minutes to add to local time to get UTC, so
    # BST is "-60". Glowmarkt labels each bucket with its local wall-clock start as if it were UTC; this says which
    # local time that was (see zone()).
    offset: str = Field(pattern=r"^-?\d+$")
    # The meter's "electricity consumption" resource the readings are of.
    resource_id: str
    hourly: Readings  # period PT1H
    daily: Readings  # period P1D
    monthly: Readings  # period P1M

    def zone(self) -> timezone:
        """Return the fixed-offset zone the readings were asked for in: the screen's, at `fetched_at`'s offset."""
        return timezone(timedelta(minutes=-int(self.offset)))

    def readings(self, period: str) -> Readings:
        """Return the readings of one of Glowmarkt's periods, "PT1H", "P1D" or "P1M"."""
        match period:
            case "PT1H":
                return self.hourly
            case "P1D":
                return self.daily
            case "P1M":
                return self.monthly
        msg = f"No stored readings for period {period!r}"
        raise ValueError(msg)
