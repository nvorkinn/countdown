"""TfL's stops and their arrivals, for the TfL provider."""

import logging
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.tfl.models import (
    ArrivalUnion,
    BusArrival,
    MetroStopPoint,
    SingleStopPoint,
    StopPoint,
    StopPointUnion,
    TubeArrival,
)

if TYPE_CHECKING:
    import requests

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
    """London's stops for the TfL provider: Tube stations and bus stops, by NaPTAN id."""

    def __init__(self, session: requests.Session, app_key: str = "") -> None:
        """`session` to ask TfL with; `app_key`, TfL's API key, is sent with every request if set."""
        self.session = session
        self.params = {"app_key": app_key} if app_key else {}

    def resolve(self, stop_id: str) -> SingleStopPoint | MetroStopPoint | None:
        """Look the stop up: None if it isn't a Tube station or bus stop; raises if TfL can't be asked."""
        response = self.session.get(f"{TFL_URL}/StopPoint/{stop_id}", params=self.params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return _find_stop_child(TypeAdapter(StopPointUnion).validate_python(response.json()), stop_id)

    def arrivals(self, stop: SingleStopPoint | MetroStopPoint) -> list[BusArrival | TubeArrival]:
        """Fetch the stop's arrivals, in TfL's order (not soonest first); raises if TfL can't be asked."""
        response = self.session.get(
            f"{TFL_URL}/StopPoint/{stop.naptan_id}/Arrivals", params=self.params, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        return TypeAdapter(list[ArrivalUnion]).validate_python(response.json())
