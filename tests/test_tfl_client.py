"""TfL's stop lookup (countdown_shared.tfl.tfl_client.TflSource.resolve), against answers shaped like TfL's own.

A stop is found inside the tree TfL answers for it. For a big interchange that tree has places of every kind, and
none of them may fail the lookup of the one that's wanted.
"""

import pytest
import requests
import responses

from countdown_shared.tfl.models import MetroStopPoint, SingleStopPoint
from countdown_shared.tfl.tfl_client import TFL_URL, TflSource

HEATHROW = "940GZZLUHRC"


def _stop(naptan_id, stop_type, modes, *, children=(), **more):
    return {
        "naptanId": naptan_id,
        "commonName": more.pop("commonName", naptan_id),
        "modes": modes,
        "stopType": stop_type,
        "additionalProperties": [],
        "children": list(children),
        **more,
    }


# Heathrow Terminals 2 & 3, cut down to what broke its lookup: an interchange whose modes and children include the
# Elizabeth line and the airport itself, bus stops with no letter on their flag, and a kind of place we've no name for.
INTERCHANGE = _stop(
    "HUBHX3",
    "TransportInterchange",
    ["bus", "elizabeth-line", "national-rail", "plane", "tube"],
    children=[
        _stop(
            "910GHTRWAPT",
            "NaptanRailStation",
            ["elizabeth-line"],
            children=[_stop("9100HTRWAPT1", "NaptanRailAccessArea", ["national-rail", "elizabeth-line"])],
        ),
        _stop(
            HEATHROW,
            "NaptanMetroStation",
            ["tube"],
            commonName="Heathrow Terminals 2 & 3 Underground Station",
            lines=[{"id": "piccadilly", "name": "Piccadilly"}],
            children=[_stop("9400ZZLUHRC1", "NaptanMetroPlatform", ["tube"])],
        ),
        _stop(
            "910GHTRWTM4",
            "NaptanRailStation",
            ["bus"],
            children=[
                _stop("490008210N", "NaptanPublicBusCoachTram", ["bus"], lines=[{"id": "285", "name": "285"}]),
                _stop("490008210S", "NaptanPublicBusCoachTram", ["bus"], stopLetter="S"),
            ],
        ),
        _stop("9200LHR", "NaptanAirAccessArea", ["plane"]),
        # A place with nothing but what every place has.
        {"naptanId": "HUBHX3X", "commonName": "Somewhere", "stopType": "NaptanAirportBuilding"},
    ],
)


@pytest.fixture
def tfl():
    with responses.RequestsMock() as mock:
        yield mock


def _answer(tfl, stop_id, body):
    tfl.get(f"{TFL_URL}/StopPoint/{stop_id}", json=body)
    return TflSource(requests.Session(), "app-key").resolve(stop_id)


def test_a_tube_station_is_found_inside_an_interchange_with_modes_and_places_of_every_kind(tfl):
    stop = _answer(tfl, HEATHROW, INTERCHANGE)

    assert isinstance(stop, MetroStopPoint)
    assert stop.common_name == "Heathrow Terminals 2 & 3 Underground Station"
    assert [line.id for line in stop.lines] == ["piccadilly"]
    assert tfl.calls[0].request.params == {"app_key": "app-key"}


def test_a_bus_stop_is_found_with_or_without_a_letter(tfl):
    lettered = _answer(tfl, "490008210S", INTERCHANGE)
    unlettered = _answer(tfl, "490008210N", INTERCHANGE)

    assert isinstance(lettered, SingleStopPoint)
    assert isinstance(unlettered, SingleStopPoint)
    assert (lettered.stop_letter, unlettered.stop_letter) == ("S", None)
    assert [line.id for line in unlettered.lines] == ["285"]


@pytest.mark.parametrize("stop_id", ["910GHTRWAPT", "9200LHR", "HUBHX3", "HUBHX3X", "not-in-the-tree"])
def test_a_place_with_no_departures_of_its_own_or_not_there_is_no_stop(tfl, stop_id):
    """A rail station, the airport, the interchange itself, a kind of place we don't know: None, not an error."""
    assert _answer(tfl, stop_id, INTERCHANGE) is None


def test_a_stop_answered_by_itself_is_found(tfl):
    stop = _answer(tfl, HEATHROW, INTERCHANGE["children"][1])

    assert isinstance(stop, MetroStopPoint)
    assert stop.naptan_id == HEATHROW


def test_tfl_failing_still_raises(tfl):
    tfl.get(f"{TFL_URL}/StopPoint/{HEATHROW}", status=503)

    with pytest.raises(requests.HTTPError):
        TflSource(requests.Session()).resolve(HEATHROW)
