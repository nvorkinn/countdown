"""TfL's departures from the store (`v1:departures:<stop id>`, written by the TfL provider) instead of from TfL.

The departures client reads every TfL stop this way; TflSource is the TfL provider's.
A bus stop's panel is a row per route, badged with its number, under the stop's name and the letter on its flag. A
Tube station's is a row per line and destination, badged with the line's initial, under the roundel and the station's
name less " Underground Station". The provider has already turned TfL's arrivals into departures (the Tube's
`towards` as the destination, trains terminating at the station left out).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from countdown_core.core import moment
from countdown_core.store import keys, reader
from countdown_core.store.models import StopDepartures
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import redis

    from countdown_core.core.style import Style
    from countdown_core.store.models import Departure


class NoStoredDeparturesError(reader.NoStoredDataError):
    """The store has nothing for the stop: its provider is down (its key expired), or hasn't fetched it yet."""


@dataclass(frozen=True)
class StoredArrival:
    """A stored departure as the panels want it: seconds to go, counted when the panel is made."""

    line: str
    destination: str
    time_to_station: int  # seconds

    @classmethod
    def of(cls, departure: Departure, now: datetime) -> StoredArrival:
        """Count `departure`'s seconds to go from `now`."""
        return cls(departure.line, departure.destination, departure.time_to_station(now))


class StoredArrivalPanel(AbstractArrivalPanel[StoredArrival]):
    """A TfL stop's departures from the store, drawn as a bus stop's or a Tube station's, by its stored mode."""

    def __init__(self, stored: StopDepartures, style: Style = "hero", now: datetime | None = None) -> None:
        """Draw `stored` in `style`, counting down from `now` (by default, now)."""
        now = now or moment.now()
        super().__init__([StoredArrival.of(departure, now) for departure in stored.departures], style)
        self.stored = stored

    @property
    @override
    def stop_name(self) -> str:
        return self.stored.name.removesuffix(" Underground Station") if self.is_tube else self.stored.name

    @property
    @override
    def stop_letter(self) -> str | None:
        return self.stored.stop_letter

    @property
    @override
    def is_tube(self) -> bool:
        return self.stored.mode == "tube"

    # A bus stop's: a row per route, badged with its number. The base calls these (static) from its __init__, before
    # self.stored is set, so the Tube's differ by subclass (StoredTubePanel), chosen by stored_panel().
    @staticmethod
    @override
    def _route_key(arrival: StoredArrival) -> tuple[str, str]:
        return arrival.line, ""

    @staticmethod
    @override
    def _badge(arrival: StoredArrival) -> str:
        return arrival.line

    @staticmethod
    @override
    def _destination(arrival: StoredArrival) -> str:
        return arrival.destination


class StoredTubePanel(StoredArrivalPanel):
    """A Tube station's: a row per line and direction, badged with the line's initial."""

    @staticmethod
    @override
    def _route_key(arrival: StoredArrival) -> tuple[str, str]:
        return arrival.line, arrival.destination

    @staticmethod
    @override
    def _badge(arrival: StoredArrival) -> str:
        return arrival.line[0]


def stored_panel(stored: StopDepartures, style: Style = "hero", now: datetime | None = None) -> StoredArrivalPanel:
    """Return the panel for a stored stop: a Tube station's or a bus stop's."""
    panel_class = StoredTubePanel if stored.mode == "tube" else StoredArrivalPanel
    return panel_class(stored, style, now)


class StoredTflSource:
    """TfL stops for the departures client, read from the store rather than asked of TfL."""

    def __init__(self, client: redis.Redis, now: Callable[[], datetime] = moment.now) -> None:
        """Read from `client`; `now` is what departures count down from (a parameter for the tests)."""
        self.client = client
        self.now = now

    def resolve(self, stop_id: str) -> str:
        """Return the stop id itself: the store says what the stop is with its departures, and may not have it yet.

        Never None and never raises, so a stop the provider hasn't fetched yet isn't dropped for good.
        """
        return stop_id

    def panel(self, stop_id: str, style: Style) -> StoredArrivalPanel:
        """Read the stop's departures; raises NoStoredDeparturesError if there are none (RedisError if it can't ask)."""
        stored = reader.read(self.client, keys.departures(stop_id), StopDepartures)
        if stored is None:
            msg = f"No departures in the store for TfL stop {stop_id}"
            raise NoStoredDeparturesError(msg)
        return stored_panel(stored, style, self.now())
