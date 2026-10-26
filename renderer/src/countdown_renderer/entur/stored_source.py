"""Entur's departures from the store (`v1:departures:<stop id>`, written by the Entur provider) instead of from Entur.

The departures client reads every Entur stop this way; EnturSource is the Entur provider's.
The panel is an EnturArrivalPanel, so it draws exactly as one from Entur does: the stored model keeps the stop's name,
its quay code and modes, and each departure's mode, and is turned back into Entur's stop and departures to draw.
"""

from typing import TYPE_CHECKING

from countdown_renderer import moment
from countdown_renderer.entur.entur_arrival_panel import EnturArrivalPanel
from countdown_renderer.tfl.stored_source import NoStoredDeparturesError
from countdown_shared.entur.entur_client import DEPARTED_GRACE, EnturDeparture, EnturStop, TransportMode
from countdown_shared.store import keys, reader
from countdown_shared.store.models import StopDepartures

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import redis

    from countdown_renderer.style import Style
    from countdown_shared.store.models import Departure


def _entur_departure(departure: Departure, now: datetime) -> EnturDeparture | None:
    """Return `departure` as Entur's client would have as of `now`, or None if it has gone (as the client drops it)."""
    # Whole seconds, truncated, as EnturClient.get_departures counts them (not Departure.time_to_station's ceiling),
    # so a stored stop's minutes match a fetched one's.
    seconds = int((departure.expected_at - now).total_seconds())
    if seconds < -DEPARTED_GRACE:
        return None
    return EnturDeparture(
        line=departure.line,
        destination=departure.destination,
        mode=TransportMode.from_str(departure.mode),
        expected_departure_time=departure.expected_at,
        time_to_station=max(0, seconds),
        quay_code=departure.platform,
    )


class StoredEnturPanel(EnturArrivalPanel):
    """An Entur stop's departures from the store, drawn as EnturArrivalPanel draws Entur's own."""

    def __init__(self, stored: StopDepartures, style: Style = "hero", now: datetime | None = None) -> None:
        """Draw `stored` in `style`, counting down from `now` (by default, now)."""
        now = now or moment.now()
        stop = EnturStop(
            id=stored.stop_id,
            name=stored.name,
            public_code=stored.stop_letter,
            transport_modes=[TransportMode.from_str(mode) for mode in stored.modes],
        )
        departures = [d for departure in stored.departures if (d := _entur_departure(departure, now)) is not None]
        super().__init__(stop, departures, style)
        self.stored = stored


class StoredEnturSource:
    """Entur stops for the departures client, read from the store rather than asked of Entur."""

    def __init__(self, client: redis.Redis, now: Callable[[], datetime] = moment.now) -> None:
        """Read from `client`; `now` is what departures count down from (a parameter for the tests)."""
        self.client = client
        self.now = now

    def resolve(self, stop_id: str) -> str:
        """Return the stop id itself: the store says what the stop is with its departures, and may not have it yet.

        Never None and never raises, so a stop the provider hasn't fetched yet isn't dropped for good.
        """
        return stop_id

    def panel(self, stop_id: str, style: Style) -> StoredEnturPanel:
        """Read the stop's departures; raises NoStoredDeparturesError if there are none (RedisError if it can't ask)."""
        stored = reader.read(self.client, keys.departures(stop_id), StopDepartures)
        if stored is None:
            msg = f"No departures in the store for Entur stop {stop_id}"
            raise NoStoredDeparturesError(msg)
        return StoredEnturPanel(stored, style, self.now())
