"""The TfL provider's mapping from TfL's stops and arrivals to the stored StopDepartures, with TflSource mocked."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from countdown_core.store import keys
from countdown_core.store.models import Departure, StopDepartures
from countdown_core.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_core.tfl.tfl_client import TflSource
from countdown_providers.tfl.main import TflProvider, to_stop_departures

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

BUS_STOP = SingleStopPoint.model_validate(
    {
        "naptanId": "490000123W",
        "commonName": "Elephant & Castle",
        "modes": ["bus"],
        "additionalProperties": [],
        "stopType": "NaptanPublicBusCoachTram",
        "stopLetter": "W",
    }
)
TUBE_STATION = MetroStopPoint.model_validate(
    {
        "naptanId": "940GZZLUKNG",
        "commonName": "Kennington Underground Station",
        "modes": ["tube"],
        "additionalProperties": [],
        "stopType": "NaptanMetroStation",
    }
)


def bus(line, destination, seconds):
    return BusArrival.model_validate(
        {
            "naptanId": BUS_STOP.naptan_id,
            "lineName": line,
            "timeToStation": seconds,
            "modeName": "bus",
            "destinationName": destination,
        }
    )


def train(towards, seconds, destination_naptan_id=None):
    return TubeArrival.model_validate(
        {
            "naptanId": TUBE_STATION.naptan_id,
            "lineName": "Northern",
            "timeToStation": seconds,
            "modeName": "tube",
            "towards": towards,
            "destinationNaptanId": destination_naptan_id,
        }
    )


def make_provider(stops):
    """Make a provider, and its mocked source, which resolves the NaPTAN ids in `stops` (id -> stop)."""
    source = MagicMock(spec=TflSource)
    source.resolve.side_effect = stops.get
    return TflProvider(source, now=lambda: NOW), source


def test_bus_stop_maps_to_stop_departures_soonest_first():
    stored = to_stop_departures(
        BUS_STOP, [bus("12", "Oxford Circus, Regent Street", 300), bus("171", "Elephant & Castle", 60)], NOW
    )
    assert stored == StopDepartures(
        fetched_at=NOW,
        stop_id="490000123W",
        name="Elephant & Castle",
        mode="bus",
        stop_letter="W",
        departures=[
            Departure(line="171", destination="Elephant & Castle", expected_at=NOW + timedelta(seconds=60)),
            # TfL's model has already trimmed the destination at the comma.
            Departure(line="12", destination="Oxford Circus", expected_at=NOW + timedelta(seconds=300)),
        ],
    )


def test_tube_station_uses_towards_and_drops_trains_terminating_there():
    stored = to_stop_departures(
        TUBE_STATION,
        [train("Morden", 120), train("Kennington", 30, destination_naptan_id=TUBE_STATION.naptan_id)],
        NOW,
    )
    assert stored.mode == "tube"
    assert stored.stop_letter is None
    assert [(d.destination, d.time_to_station(NOW)) for d in stored.departures] == [("Morden", 120)]


def test_stop_with_nothing_due_still_maps():
    assert to_stop_departures(BUS_STOP, [], NOW).departures == []


def test_time_to_station_counts_down_from_now_and_stops_at_zero():
    departure = Departure(line="12", destination="X", expected_at=NOW + timedelta(seconds=90))
    assert departure.time_to_station(NOW + timedelta(seconds=30)) == 60
    assert departure.time_to_station(NOW + timedelta(seconds=120)) == 0


def test_fetch_returns_a_model_per_stop():
    provider, source = make_provider({BUS_STOP.naptan_id: BUS_STOP, TUBE_STATION.naptan_id: TUBE_STATION})
    source.arrivals.side_effect = lambda stop: [bus("12", "Peckham", 60)] if stop is BUS_STOP else [train("Morden", 30)]

    results = asyncio.run(provider.fetch([BUS_STOP.naptan_id, TUBE_STATION.naptan_id]))

    assert set(results) == {BUS_STOP.naptan_id, TUBE_STATION.naptan_id}
    assert results[BUS_STOP.naptan_id].departures[0].line == "12"
    assert results[TUBE_STATION.naptan_id].departures[0].destination == "Morden"
    assert provider.output_key(BUS_STOP.naptan_id) == keys.departures(BUS_STOP.naptan_id)


def test_fetch_leaves_out_unknown_and_failing_stops_without_raising(caplog):
    provider, source = make_provider({BUS_STOP.naptan_id: BUS_STOP, TUBE_STATION.naptan_id: TUBE_STATION})

    def arrivals(stop):
        if stop is TUBE_STATION:
            msg = "TfL said 500"
            raise RuntimeError(msg)
        return []

    source.arrivals.side_effect = arrivals

    results = asyncio.run(provider.fetch(["not-a-stop", TUBE_STATION.naptan_id, BUS_STOP.naptan_id]))

    assert list(results) == [BUS_STOP.naptan_id]
    assert "isn't a bus stop or Tube station" in caplog.text
    assert "TfL said 500" in caplog.text


def test_fetch_resolves_each_stop_only_once():
    provider, source = make_provider({BUS_STOP.naptan_id: BUS_STOP})
    source.arrivals.return_value = []
    asyncio.run(provider.fetch([BUS_STOP.naptan_id]))
    asyncio.run(provider.fetch([BUS_STOP.naptan_id]))
    assert source.resolve.call_count == 1
    assert source.arrivals.call_count == 2


def test_an_unresolvable_stop_is_asked_again_next_time():
    # It may only have failed for now (or been added to TfL since).
    provider, source = make_provider({})
    asyncio.run(provider.fetch(["not-a-stop"]))
    asyncio.run(provider.fetch(["not-a-stop"]))
    assert source.resolve.call_count == 2
