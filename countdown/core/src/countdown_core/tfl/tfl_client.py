import logging

import requests
from pydantic import TypeAdapter

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Style
from countdown_core.tfl.bus_arrival_panel import BusArrivalPanel
from countdown_core.tfl.models import (
    ArrivalUnion,
    MetroStopPoint,
    SingleStopPoint,
    StopPoint,
    StopPointUnion,
)
from countdown_core.tfl.tube_arrival_panel import TubeArrivalPanel

logger = logging.getLogger(__name__)

TFL_URL = "https://api.tfl.gov.uk"


def _find_stop_child(stop: StopPoint, naptan_id: str) -> SingleStopPoint | MetroStopPoint | None:
    if stop.naptan_id == naptan_id and isinstance(stop, (SingleStopPoint, MetroStopPoint)):
        return stop
    for child in stop.children:
        grandchild = _find_stop_child(child, naptan_id)
        if grandchild is not None:
            return grandchild
    return None


class TflSource:
    """London's stops for the departures client: Tube stations and bus stops, by NaPTAN id."""

    def __init__(self, session: requests.Session, app_key: str = ""):
        self.session = session
        self.params = {"app_key": app_key} if app_key else {}

    def resolve(self, stop_id: str) -> SingleStopPoint | MetroStopPoint | None:
        """The stop, or None if it isn't a Tube station or bus stop; raises if TfL can't be asked."""
        response = self.session.get(f"{TFL_URL}/StopPoint/{stop_id}", params=self.params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return _find_stop_child(TypeAdapter(StopPointUnion).validate_python(response.json()), stop_id)

    def panel(self, stop: SingleStopPoint | MetroStopPoint, style: Style) -> AbstractArrivalPanel:
        """The stop's arrivals, as a panel that groups them by route."""
        response = self.session.get(
            f"{TFL_URL}/StopPoint/{stop.naptan_id}/Arrivals", params=self.params, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        arrivals = TypeAdapter(list[ArrivalUnion]).validate_python(response.json())
        if isinstance(stop, MetroStopPoint):
            return TubeArrivalPanel(stop, arrivals, style)
        return BusArrivalPanel(stop, arrivals, style)
