import logging
from collections.abc import Callable
from typing import TypeVar

from pydantic import TypeAdapter

from countdown_core.config_server.models import TflConfig
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.panel import Panel
from countdown_core.tfl.bus_arrival_panel import BusArrivalPanel
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.tfl.models import (
    ArrivalUnion,
    BusArrival,
    MetroStopPoint,
    SingleStopPoint,
    StopPoint,
    StopPointUnion,
    TubeArrival,
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


def _unique_list_by_route(arrivals: list[T], key_builder: Callable[[T], str]):
    seen = set()
    unique_list = []
    for arrival in arrivals:
        key = key_builder(arrival)
        if key not in seen:
            seen.add(key)
            unique_list.append(arrival)
    return unique_list


T = TypeVar("T", BusArrival, TubeArrival)


class TflClient(AbstractClient):
    panel_title = "Arrivals"

    def __init__(self, registration: Registration, config: TflConfig):
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

    def _get_next_stop(self) -> SingleStopPoint | MetroStopPoint:
        if not self.stops:
            raise ValueError("No stops initialised")
        stop = self.stops[self.current_stop]
        self.current_stop += 1
        if self.current_stop >= len(self.stops):
            self.current_stop = 0
        return stop

    def _update(self) -> Panel:
        if not self.stops:
            return self.message_panel("Needs configuring")
        stop_and_arrivals = []
        # All of them, in order, when they fit; otherwise the next page of them.
        if len(self.stops) <= self.stops_per_update:
            to_fetch = list(self.stops)
        else:
            to_fetch = [self._get_next_stop() for _ in range(self.stops_per_update)]
        count_to_fetch = len(to_fetch)
        for stop in to_fetch:
            try:
                response = self.session.get(
                    f"https://api.tfl.gov.uk/StopPoint/{stop.naptan_id}/Arrivals",
                    params=self.params,
                    timeout=DEFAULT_TIMEOUT,
                )
                response.raise_for_status()
                json = response.json()
                arrivals = TypeAdapter(list[ArrivalUnion]).validate_python(json)
                sortd = sorted(arrivals, key=lambda x: x.time_to_station)
                if isinstance(stop, SingleStopPoint):
                    unique = _unique_list_by_route(sortd, lambda a: a.line)
                    stop_and_arrivals.append(BusArrivalPanel(stop, unique))
                elif isinstance(stop, MetroStopPoint):
                    unique = _unique_list_by_route(sortd, lambda a: f"{a.line}-{a.towards}")
                    stop_and_arrivals.append(TubeArrivalPanel(stop, unique))
            except Exception as e:
                logger.exception(f"Error fetching arrivals for stop {stop.naptan_id}: {e}")
        if not stop_and_arrivals:
            # Raised rather than shown, so the registry keeps the last good arrivals.
            raise RuntimeError(f"Could not fetch arrivals for any of {count_to_fetch} TfL stops")
        return CombinedArrivalPanel(stop_and_arrivals)
