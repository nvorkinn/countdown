import pytest
from pydantic import TypeAdapter
from tfl_fixtures import BUS_STOP_JSON, METRO_STOP_JSON

from countdown_shared.glow.models import Readings
from countdown_shared.tfl.models import (
    ArrivalUnion,
    BusArrival,
    MetroStopPoint,
    SingleStopPoint,
    StopPointUnion,
    TubeArrival,
)

BUS_ARRIVAL_JSON = {
    "naptanId": "490000123W",
    "lineName": "N155",
    "timeToStation": 300,
    "modeName": "bus",
    "destinationName": "Elephant & Castle",
}

TUBE_ARRIVAL_JSON = {
    "naptanId": "940GZZLUKNG",
    "lineName": "Northern",
    "timeToStation": 120,
    "modeName": "tube",
    "towards": "Bank",
    "destinationNaptanId": "940GZZLUEAC",
}


def test_bus_arrival_discriminated_union():
    arrival = TypeAdapter(ArrivalUnion).validate_python(BUS_ARRIVAL_JSON)
    assert isinstance(arrival, BusArrival)
    assert arrival.destination == "Elephant & Castle"
    assert arrival.time_to_station == 300


@pytest.mark.parametrize(
    ("destination_name", "expected"),
    [
        ("Crystal Palace, Bus Station", "Crystal Palace"),
        ("Elephant & Castle", "Elephant & Castle"),
        ("  Peckham , Rye Lane, SE15 ", "Peckham"),
        (", Rye Lane", ", Rye Lane"),
    ],
)
def test_a_bus_destination_is_trimmed_to_the_part_before_the_first_comma(destination_name, expected):
    arrival = TypeAdapter(ArrivalUnion).validate_python({**BUS_ARRIVAL_JSON, "destinationName": destination_name})
    assert arrival.destination == expected


def test_tube_arrival_discriminated_union():
    arrival = TypeAdapter(ArrivalUnion).validate_python(TUBE_ARRIVAL_JSON)
    assert isinstance(arrival, TubeArrival)
    assert arrival.towards == "Bank"


def test_single_stop_point_discriminated_union():
    stop = TypeAdapter(StopPointUnion).validate_python(BUS_STOP_JSON)
    assert isinstance(stop, SingleStopPoint)
    assert stop.stop_letter == "W"


def test_metro_stop_point_discriminated_union():
    stop = TypeAdapter(StopPointUnion).validate_python(METRO_STOP_JSON)
    assert isinstance(stop, MetroStopPoint)


def test_metro_stop_point_with_nested_children():
    parent = dict(METRO_STOP_JSON)
    parent["children"] = [BUS_STOP_JSON]
    stop = TypeAdapter(StopPointUnion).validate_python(parent)
    assert len(stop.children) == 1
    assert isinstance(stop.children[0], SingleStopPoint)


def test_readings_parses_data_tuples():
    readings = Readings.model_validate({"data": [[1700000000, 1.5], [1700003600, 2.25]]})
    assert readings.data == [(1700000000, 1.5), (1700003600, 2.25)]
