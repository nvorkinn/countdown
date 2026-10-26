"""TfL's stops, their arrivals and TfL's disruptions, for the TfL providers (departures and notices)."""

import logging
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from countdown_core.core.http_session import DEFAULT_TIMEOUT
from countdown_core.tfl.models import (
    ArrivalUnion,
    BusArrival,
    DisruptedPoint,
    Line,
    MetroStopPoint,
    RoadDisruption,
    SingleStopPoint,
    StopPoint,
    StopPointUnion,
    TubeArrival,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

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


Lines = TypeAdapter(list[Line])
DisruptedPoints = TypeAdapter(list[DisruptedPoint])
RoadDisruptions = TypeAdapter(list[RoadDisruption])


class TflSource:
    """TfL's unified API for the TfL providers: Tube stations and bus stops by NaPTAN id, and the disruptions."""

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

    def line_status(self, line_ids: Iterable[str]) -> list[Line]:
        """Fetch the lines' statuses (/Line/{ids}/Status), in TfL's order; raises if TfL can't be asked."""
        return Lines.validate_json(self._get(f"/Line/{','.join(line_ids)}/Status"))

    def stop_disruptions(self, stop_id: str) -> list[DisruptedPoint]:
        """Fetch what's wrong at a stop (/StopPoint/{id}/Disruption), such as a lift; raises if TfL can't be asked."""
        return DisruptedPoints.validate_json(self._get(f"/StopPoint/{stop_id}/Disruption"))

    def road_disruptions(self, severities: Iterable[str]) -> list[RoadDisruption]:
        """Fetch London's road disruptions of `severities` (TfL's names, "Severe"...); raises if TfL can't be asked."""
        return RoadDisruptions.validate_json(self._get("/Road/all/Disruption", severities=",".join(severities)))

    def _get(self, path: str, **params: str) -> bytes:
        response = self.session.get(f"{TFL_URL}{path}", params=self.params | params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        # The raw body, before pydantic drops the fields the models don't declare: turn on debug logging for this
        # module to see everything TfL actually sends.
        logger.debug("GET %s: %s", path, response.text)
        return response.content
