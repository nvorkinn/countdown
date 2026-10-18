"""TfL's API responses (and postcodes.io's), as far as the dashboard reads them."""

from abc import ABC
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class TubeArrival(Arrival):
    """A train due at a Tube station."""

    mode_name: Literal[Mode.TUBE] = Field(alias="modeName")
    towards: str
    destination_naptan_id: str | None = Field(alias="destinationNaptanId", default=None)


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
    """A place in TfL's stop hierarchy (a station, a stop, an entrance...), with the ones inside it."""

    naptan_id: str = Field(alias="naptanId")
    common_name: str = Field(alias="commonName")
    modes: list[Mode]
    additional_properties: list[AdditionalProperty] = Field(alias="additionalProperties")
    children: list[StopPointUnion] = []


class OtherStopPoint(StopPoint):
    """A stop point that has no departures of its own to show: a cluster, an entrance, a platform..."""

    stop_type: Literal[
        StopType.NAPTAN_ONSTREET_BUS_COACH_STOP_PAIR,
        StopType.TRANSPORT_INTERCHANGE,
        StopType.NAPTAN_ONSTREET_BUS_COACH_STOP_CLUSTER,
        StopType.NAPTAN_RAIL_STATION,
        StopType.NAPTAN_RAIL_ENTRANCE,
        StopType.NAPTAN_RAIL_ACCESS_AREA,
        StopType.NAPTAN_METRO_ENTRANCE,
        StopType.NAPTAN_METRO_ACCESS_AREA,
        StopType.NAPTAN_METRO_PLATFORM,
        StopType.NAPTAN_BUS_COACH_STATION,
    ] = Field(alias="stopType")


class SingleStopPoint(StopPoint):
    """A single bus or tram stop, with the letter on its flag."""

    stop_type: Literal[StopType.NAPTAN_PUBLIC_BUS_COACH_TRAM] = Field(alias="stopType")
    stop_letter: str = Field(alias="stopLetter")
    lines: list[Identifier] = []


class MetroStopPoint(StopPoint):
    """A Tube station."""

    stop_type: Literal[StopType.NAPTAN_METRO_STATION] = Field(alias="stopType")
    lines: list[Identifier] = []


StopPointUnion = Annotated[OtherStopPoint | SingleStopPoint | MetroStopPoint, Field(discriminator="stop_type")]


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


class Postcode(BaseModel):
    """The parts of postcodes.io's /postcodes/{postcode} the notice board uses."""

    postcode: str
    latitude: float
    longitude: float
    country: str
    # English region ("London", "South East", ...); null outside England.
    region: str | None = None
    # Local authority, e.g. "Southwark" or, in Scotland, the council area.
    admin_district: str | None = None


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
