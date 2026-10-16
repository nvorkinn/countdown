import logging

from pydantic import TypeAdapter

from countdown_api.models import Tfl
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.panel import Panel
from countdown_core.tfl.bus_arrival_panel import BusArrivalPanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel, Rotation
from countdown_core.tfl.models import (
    ArrivalUnion,
    MetroStopPoint,
    SingleStopPoint,
    StopPoint,
    StopPointUnion,
)
from countdown_core.tfl.tube_arrival_panel import TubeArrivalPanel
from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)


def _find_stop_child(stop: StopPoint, naptan_id: str) -> SingleStopPoint | MetroStopPoint | None:
    if stop.naptan_id == naptan_id and isinstance(stop, (SingleStopPoint, MetroStopPoint)):
        return stop
    for child in stop.children:
        grandchild = _find_stop_child(child, naptan_id)
        if grandchild is not None:
            return grandchild
    return None


class TflClient(AbstractClient):
    panel_title = "Arrivals"

    def __init__(self, registration: Registration, config: Tfl):
        """Never touches the network: stops are resolved in _initialise()."""
        super().__init__(registration, config)
        self.stops: list[SingleStopPoint | MetroStopPoint] = []
        self.current_stop = 0
        self.params = {"app_key": config.app_key} if config.app_key else {}
        # Set by the layout (ApiRegistry.show_stops).
        self.stops_per_update = 2

    def _initialise(self) -> None:
        """Resolves the configured stop ids. Fails (to be retried) only if none resolve."""
        if self.stops:
            return
        resolved = self.init_stops(self.config.stop_ids)
        if self.config.stop_ids and not resolved:
            raise RuntimeError(f"None of the {len(self.config.stop_ids)} configured TfL stops could be resolved")
        self.stops = resolved

    def init_stops(self, stop_ids: list[str]) -> list[SingleStopPoint | MetroStopPoint]:
        stops: list[SingleStopPoint | MetroStopPoint] = []
        for stop_id in stop_ids:
            try:
                info = self._get_stop_info(stop_id)
                if info is not None:
                    stops.append(info)
                else:
                    logger.warning(f"Stop ID {stop_id} could not be resolved from TfL API")
            except Exception as e:
                logger.warning(f"Error fetching stop ID {stop_id}: {e}", exc_info=True)
        return stops

    def _get_stop_info(self, stop_id: str) -> SingleStopPoint | MetroStopPoint | None:
        response = self.session.get(
            f"https://api.tfl.gov.uk/StopPoint/{stop_id}", params=self.params, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        json = response.json()
        info = TypeAdapter(StopPointUnion).validate_python(json)
        return _find_stop_child(info, stop_id)

    def _next_stop_index(self) -> int:
        """The index of the next stop to show."""
        if not self.stops:
            raise ValueError("No stops initialised")
        index = self.current_stop
        self.current_stop += 1
        if self.current_stop >= len(self.stops):
            self.current_stop = 0
        return index

    def _update(self) -> Panel:
        if not self.stops:
            return self.message_panel("Needs configuring")
        stop_and_arrivals = []
        shown: set[int] = set()
        # All of them, in order, when they fit; otherwise the next page of them.
        rotating = len(self.stops) > self.stops_per_update
        if rotating:
            to_fetch = [self._next_stop_index() for _ in range(self.stops_per_update)]
        else:
            to_fetch = list(range(len(self.stops)))
        count_to_fetch = len(to_fetch)
        for index in to_fetch:
            stop = self.stops[index]
            try:
                response = self.session.get(
                    f"https://api.tfl.gov.uk/StopPoint/{stop.naptan_id}/Arrivals",
                    params=self.params,
                    timeout=DEFAULT_TIMEOUT,
                )
                response.raise_for_status()
                json = response.json()
                arrivals = TypeAdapter(list[ArrivalUnion]).validate_python(json)
                # The panels group them by route, keeping the later ones for the styles that show them.
                if isinstance(stop, SingleStopPoint):
                    stop_and_arrivals.append(BusArrivalPanel(stop, arrivals, self.config.style))
                elif isinstance(stop, MetroStopPoint):
                    stop_and_arrivals.append(TubeArrivalPanel(stop, arrivals, self.config.style))
                shown.add(index)
            except Exception as e:
                logger.exception(f"Error fetching arrivals for stop {stop.naptan_id}: {e}")
        if not stop_and_arrivals:
            # Raised rather than shown, so the registry keeps the last good arrivals.
            raise RuntimeError(f"Could not fetch arrivals for any of {count_to_fetch} TfL stops")
        # Only when they rotate: then the squares under the stops say which are on screen.
        rotation = Rotation(frozenset(shown), len(self.stops)) if rotating else None
        return CombinedArrivalPanel(stop_and_arrivals, rotation)
