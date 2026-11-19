"""TfL's API responses (and postcodes.io's), as far as the dashboard reads them."""

from abc import ABC
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator

# Where a postcode is, in the fields the notice board uses, is the store contract's (countdown-api's Postcode, which the
# value of `v1:location:<place>` holds), imported from there: postcodes.io's answer is read straight into it, and the
# notice board's callers name it from here.
from countdown_api.models import Postcode


class Mode(StrEnum):
    """A TfL mode of transport, as its modeName."""

    BUS = "bus"
    TUBE = "tube"
    NATIONAL_RAIL = "national-rail"
    INTERNATIONAL_RAIL = "international-rail"


class Arrival(BaseModel, ABC):
    """One vehicle due at a stop, from TfL's /StopPoint/{id}/Arrivals."""

    naptan_id: str = Field(alias="naptanId")
    line: str = Field(alias="lineName")
    time_to_station: int = Field(alias="timeToStation")
    # A bus's registration or a train's set number: the same from one fetch to the next, so a screen can follow the
    # vehicle as predictions reorder. TfL sometimes leaves it out (a train it hasn't matched to a set yet).
    vehicle_id: str | None = Field(alias="vehicleId", default=None)


class TubeArrival(Arrival):
    """A train due at a Tube station."""

    mode_name: Literal[Mode.TUBE] = Field(alias="modeName")
    towards: str
    destination_naptan_id: str | None = Field(alias="destinationNaptanId", default=None)
    # "Northbound - Platform 1".
    platform_name: str | None = Field(alias="platformName", default=None)


class BusArrival(Arrival):
    """A bus due at a bus stop."""

    mode_name: Literal[Mode.BUS] = Field(alias="modeName")
    destination: str = Field(alias="destinationName")

    @field_validator("destination")
    @classmethod
    def trim_destination(cls, value: str) -> str:
        """Drop what TfL often adds to a destination after a comma (a road or landmark).

        It doesn't fit on the panel and isn't needed to tell buses apart. Keeps the whole name if there's nothing
        before the comma.
        """
        return value.split(",", 1)[0].strip() or value.strip()


ArrivalUnion = Annotated[BusArrival | TubeArrival, Field(discriminator="mode_name")]


class AdditionalProperty(BaseModel):
    """A key and value TfL tacks onto a stop point."""

    key: str
    value: str


class StopType(StrEnum):
    """What kind of place a stop point is, as its stopType."""

    NAPTAN_ONSTREET_BUS_COACH_STOP_PAIR = "NaptanOnstreetBusCoachStopPair"
    NAPTAN_PUBLIC_BUS_COACH_TRAM = "NaptanPublicBusCoachTram"
    NAPTAN_BUS_COACH_STATION = "NaptanBusCoachStation"
    TRANSPORT_INTERCHANGE = "TransportInterchange"
    NAPTAN_ONSTREET_BUS_COACH_STOP_CLUSTER = "NaptanOnstreetBusCoachStopCluster"
    NAPTAN_RAIL_STATION = "NaptanRailStation"
    NAPTAN_METRO_STATION = "NaptanMetroStation"
    NAPTAN_RAIL_ENTRANCE = "NaptanRailEntrance"
    NAPTAN_RAIL_ACCESS_AREA = "NaptanRailAccessArea"
    NAPTAN_METRO_ENTRANCE = "NaptanMetroEntrance"
    NAPTAN_METRO_ACCESS_AREA = "NaptanMetroAccessArea"
    NAPTAN_METRO_PLATFORM = "NaptanMetroPlatform"


class Identifier(BaseModel):
    """A line serving a stop: its id and name."""

    id: str
    name: str


class StopPoint(BaseModel):
    """A place in TfL's stop hierarchy (a station, a stop, an entrance...), with the ones inside it.

    A stop is found inside the tree TfL answers for it, which for a big interchange (Heathrow's) has places of every
    kind beside it: an airport, the Elizabeth line's station, bus stops with no letter. So nothing here is narrower
    than it must be to find the stop: a mode or a kind of place this doesn't know mustn't fail the whole lookup.
    """

    naptan_id: str = Field(alias="naptanId")
    common_name: str = Field(alias="commonName")
    # TfL's names for them, whatever they are ("tube", "elizabeth-line", "plane"...): more than Mode has.
    modes: list[str] = []
    additional_properties: list[AdditionalProperty] = Field(alias="additionalProperties", default=[])
    children: list[StopPointUnion] = []


class OtherStopPoint(StopPoint):
    """A stop point that has no departures of its own to show: a cluster, an entrance, a platform, an airport..."""

    # Any of StopType's but the two below, and any TfL has that StopType doesn't.
    stop_type: str = Field(alias="stopType")


class SingleStopPoint(StopPoint):
    """A single bus or tram stop, with the letter on its flag if it has one."""

    stop_type: Literal[StopType.NAPTAN_PUBLIC_BUS_COACH_TRAM] = Field(alias="stopType")
    # Missing for a stop with no letter (a coach stop at an airport's terminal).
    stop_letter: str | None = Field(alias="stopLetter", default=None)
    lines: list[Identifier] = []


class MetroStopPoint(StopPoint):
    """A Tube station."""

    stop_type: Literal[StopType.NAPTAN_METRO_STATION] = Field(alias="stopType")
    lines: list[Identifier] = []


def _kind_of_stop(stop: object) -> str:
    """Return which of the three models reads a stop point: by its stopType, anything unknown being an "other"."""
    stop_type = stop.get("stopType") if isinstance(stop, dict) else getattr(stop, "stop_type", None)
    if stop_type == StopType.NAPTAN_PUBLIC_BUS_COACH_TRAM:
        return "single"
    if stop_type == StopType.NAPTAN_METRO_STATION:
        return "metro"
    return "other"


StopPointUnion = Annotated[
    Annotated[OtherStopPoint, Tag("other")]
    | Annotated[SingleStopPoint, Tag("single")]
    | Annotated[MetroStopPoint, Tag("metro")],
    Discriminator(_kind_of_stop),
]


class KnownJourney(BaseModel):
    """One timetabled departure from the timetable's stop, at hour:minute of its schedule's day.

    The hour runs past 23 for a journey after midnight that belongs to the day before (25:10 is 01:10 the next day).
    """

    hour: int
    minute: int
    # Which of the route's station intervals (StationInterval.id) the journey runs: so where it ends.
    interval_id: int = Field(alias="intervalId")


class Schedule(BaseModel):
    """The journeys on the days the schedule's name says ("Monday - Thursday", "Saturday (also Good Friday)")."""

    name: str
    known_journeys: list[KnownJourney] = Field(alias="knownJourneys", default=[])


class Interval(BaseModel):
    """A stop a journey calls at after the timetable's stop."""

    stop_id: str = Field(alias="stopId")


class StationInterval(BaseModel):
    """One way the route's journeys run on from the timetable's stop: its stops in order, the last where they end."""

    id: int
    intervals: list[Interval] = []


class TimetableRoute(BaseModel):
    """A route of the line through the stop, with its schedules."""

    station_intervals: list[StationInterval] = Field(alias="stationIntervals", default=[])
    schedules: list[Schedule] = []


class Timetable(BaseModel):
    """The line's timetable from one stop in one direction."""

    # The stop the times are at: not always the one asked for, as TfL answers a stop the line doesn't serve that way
    # with another's.
    departure_stop_id: str | None = Field(alias="departureStopId", default=None)
    routes: list[TimetableRoute] = []


class TimetableStop(BaseModel):
    """A stop the timetable names."""

    id: str
    name: str


class TimetableResponse(BaseModel):
    """TfL's /Line/{id}/Timetable/{stop id}?direction=... answer.

    With no direction it answers with a disambiguation instead, and no timetable; so does a direction the line doesn't
    run from the stop.
    """

    line_id: str | None = Field(alias="lineId", default=None)
    line_name: str | None = Field(alias="lineName", default=None)
    stops: list[TimetableStop] = []
    timetable: Timetable | None = None


class StopPointResponse(BaseModel):
    """Stop points wrapped in a stopPoints list, as some TfL endpoints return them."""

    stop_points: list[StopPoint] = Field(alias="stopPoints")


class TflDisruptionModel(BaseModel):
    """A model of one of TfL's disruption answers, which writes TfL's own names back out (its aliases).

    The TfL notices provider stores these answers, and the renderer reads them back with the same models: written
    under TfL's names, a stored copy reads like TfL's answer (less what the models leave out).
    """

    model_config = ConfigDict(serialize_by_alias=True)


class ValidityPeriod(TflDisruptionModel):
    """When a line status applies."""

    from_date: datetime = Field(alias="fromDate")
    to_date: datetime = Field(alias="toDate")


class LineStatus(TflDisruptionModel):
    """A line's status: Good Service, Minor Delays and the like, and why."""

    status_severity: int = Field(alias="statusSeverity")
    description: str = Field(alias="statusSeverityDescription")
    # What's actually going on, e.g. "Victoria Line: Minor delays due to a track fault at
    # Brixton." Missing for some statuses (and every Good Service).
    reason: str | None = None
    validity_periods: list[ValidityPeriod] = Field(alias="validityPeriods", default=[])


class Line(TflDisruptionModel):
    """One line from TfL's /Line/{ids}/Status."""

    id: str
    name: str
    mode_name: str = Field(alias="modeName", default="")
    line_statuses: list[LineStatus] = Field(alias="lineStatuses")


class DisruptedPoint(TflDisruptionModel):
    """One entry from TfL's /StopPoint/{ids}/Disruption.

    Lifts out of order, closed entrances, no step-free access and the like.
    """

    common_name: str = Field(alias="commonName")
    description: str
    to_date: datetime | None = Field(alias="toDate", default=None)


class RoadDisruption(TflDisruptionModel):
    """One entry from TfL's /Road/all/Disruption."""

    id: str
    severity: str
    category: str
    comments: str = ""
    # "[lon,lat]" as a string.
    point: str | None = None
    end_date_time: datetime | None = Field(alias="endDateTime", default=None)


class PostcodeResponse(BaseModel):
    """postcodes.io's envelope around a Postcode."""

    result: Postcode


class Outcode(BaseModel):
    """The centre of a postcode district ("SE17"), from postcodes.io's /outcodes/{outcode}."""

    latitude: float
    longitude: float
    # Every country the district is in (one across a border is in two).
    country: list[str] = []


class OutcodeResponse(BaseModel):
    """postcodes.io's envelope around an Outcode."""

    result: Outcode
