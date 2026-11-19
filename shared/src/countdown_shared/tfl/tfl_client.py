"""TfL's stops, their arrivals and TfL's disruptions, for the TfL providers (departures and notices)."""

import logging
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from countdown_shared.core.http_session import DEFAULT_TIMEOUT
from countdown_shared.tfl.models import (
    ArrivalUnion,
    BusArrival,
    DisruptedPoint,
    Line,
    MetroStopPoint,
    RoadDisruption,
    SingleStopPoint,
    StopPoint,
    StopPointUnion,
    TimetableResponse,
    TubeArrival,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

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

    def line_status_between(self, line_ids: Iterable[str], start: datetime, end: datetime) -> list[Line]:
        """Fetch the lines' statuses from `start` to `end`, London time; raises if TfL can't be asked.

        /Line/{ids}/Status/{start}/to/{end}: closures ahead, which the plain status only says once they've begun.
        """
        dates = f"{start.strftime('%Y-%m-%dT%H:%M:%S')}/to/{end.strftime('%Y-%m-%dT%H:%M:%S')}"
        return Lines.validate_json(self._get(f"/Line/{','.join(line_ids)}/Status/{dates}"))

    def timetable(self, line_id: str, stop_id: str, direction: str) -> TimetableResponse:
        """Fetch the line's timetable from the stop going `direction` (inbound or outbound); raises on error.

        Its `timetable` is None where the line doesn't run that way from the stop.
        """
        return TimetableResponse.model_validate_json(
            self._get(f"/Line/{line_id}/Timetable/{stop_id}", direction=direction)
        )

    def mode_status(self, modes: Iterable[str]) -> list[Line]:
        """Fetch every line of `modes` with its status (/Line/Mode/{modes}/Status), in TfL's order; raises on error."""
        return Lines.validate_json(self._get(f"/Line/Mode/{','.join(modes)}/Status"))

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
