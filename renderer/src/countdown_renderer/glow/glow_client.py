"""The energy panel's client: electricity readings from the store, written there by countdown-server's glow-provider."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, override

from countdown_api.models import Glowmarkt
from countdown_core.i18n import N_
from countdown_core.store import keys, reader
from countdown_core.store.energy import StoredEnergy
from countdown_renderer import moment, rotation
from countdown_renderer.abstract_client import AbstractClient
from countdown_renderer.glow.energy_panel import PAGES, EnergyPanel

if TYPE_CHECKING:
    from datetime import tzinfo


def _bucket_starts(
    data: list[tuple[int | float | str, float]], local_tz: tzinfo | None
) -> list[tuple[datetime, float]]:
    """Return Glow's (time, kWh) readings as (bucket start, kWh), each start an aware time in `local_tz`.

    With an offset set, Glow labels each bucket with its *local* wall-clock start encoded as if it were UTC (06:00 BST
    comes back as 06:00Z). Read the wall clock as UTC, then relabel it with the zone the readings were asked for in,
    so the starts are the instants the buckets really start at, comparable with the page's window.
    """
    readings = []
    for ts, value in data:
        # Glow has sent both epoch seconds and ISO strings.
        wall_clock = datetime.fromtimestamp(ts, tz=UTC) if isinstance(ts, (int, float)) else datetime.fromisoformat(ts)
        readings.append((wall_clock.replace(tzinfo=local_tz), value))
    return readings


class NoStoredEnergyError(reader.NoStoredDataError):
    """The store has no readings for the device: the broker's glow-provider is down, or hasn't fetched them yet."""


class GlowClient(AbstractClient[Glowmarkt]):
    """The energy panel's pages in turn, one a minute (see core.rotation): the last 24 hours, 31 days, and this year.

    The readings come from the store, under the device's id (`v1:energy:<device id>`, written by countdown-server's
    glow-provider, which signs in to Glowmarkt itself), so this renderer never does.
    """

    panel_title = N_("Energy")

    def __init__(self, device_id: str, config: Glowmarkt) -> None:
        """`config` is the broker's glowmarkt section, which only switches the panel on (see api_registry.is_enabled).

        Raises without a store.
        """
        super().__init__(device_id, config)
        self.store = reader.require_store()

    @override
    def _initialise(self) -> None:
        pass

    @override
    def _update(self) -> EnergyPanel:
        """Return this minute's page, drawn from the readings in the store; NoStoredEnergyError if there are none.

        The stored readings run to when they were fetched, from the start of each page's window then. Cut to the
        window as of now, in the zone they were asked for in, they're the buckets since the window's start, the last
        one perhaps still open.
        """
        stored = reader.read(self.store, keys.energy(self.device_id), StoredEnergy)
        if stored is None:
            msg = f"No energy readings in the store for device {self.device_id}"
            raise NoStoredEnergyError(msg)
        page_index = rotation.page(len(PAGES))
        current = moment.now().astimezone(stored.zone())
        window_start, period = self._page_config(page_index, current)
        window_start = self._truncate(window_start, period)
        buckets = dict(_bucket_starts(stored.readings(period).data, current.tzinfo))
        usage = [
            {"start": bucket_start.isoformat(), "kwh": round(value, 3)}
            for bucket_start, value in sorted(buckets.items())
            if bucket_start >= window_start
        ]
        return EnergyPanel(usage, page_index)

    def _page_config(self, page_index: int, current: datetime) -> tuple[datetime, str]:
        """Return the page's window start as of `current`, and Glow's period for its buckets."""
        match page_index:
            case 0:
                return current - timedelta(hours=24), "PT1H"
            case 1:
                return current - timedelta(days=31), "P1D"
            case 2:
                return current.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), "P1M"
        msg = f"page_index must be 0, 1 or 2 - got {page_index}"
        raise ValueError(msg)

    def _truncate(self, dt: datetime, period: str) -> datetime:
        """Round a datetime down to the start of its bucket."""
        if period == "PT1H":
            return dt.replace(minute=0, second=0, microsecond=0)
        if period == "P1D":
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        # Otherwise it's a month.
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
