"""The Entur provider against mocked Entur (responses): its mapping to the stored StopDepartures, and its fetch."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import fakeredis
import requests
import responses

from countdown_core.entur.entur_client import (
    HEADERS,
    JOURNEY_PLANNER_URL,
    EnturDeparture,
    EnturStop,
    TransportMode,
)
from countdown_core.entur.entur_source import DEPARTURES_PER_STOP, EnturSource
from countdown_core.store import keys
from countdown_core.store.models import Departure, StopDepartures
from countdown_providers.entur.main import EnturProvider, to_stop_departures
from countdown_providers.runner import ProviderRunner

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

MAJORSTUEN_ID = "NSR:StopPlace:58381"
QUAY_ID = "NSR:Quay:7333"
FAILING_ID = "NSR:StopPlace:666"
UNKNOWN_ID = "NSR:StopPlace:1"

MAJORSTUEN = {"id": MAJORSTUEN_ID, "name": "Majorstuen", "transportMode": ["metro", "tram"]}


def call(line: str, to: str, minutes: float, mode: str = "metro", quay: str = "2", **extra) -> dict:
    """One of Entur's estimatedCalls, leaving `minutes` after NOW."""
    when = (NOW + timedelta(minutes=minutes)).isoformat()
    return {
        "realtime": True,
        "cancellation": False,
        "forBoarding": True,
        "aimedDepartureTime": when,
        "expectedDepartureTime": when,
        "destinationDisplay": {"frontText": to},
        "quay": {"publicCode": quay},
        "serviceJourney": {"transportMode": mode, "line": {"publicCode": line, "name": to, "transportMode": mode}},
    } | extra


# Majorstuen: metro and tram, from several platforms, with a cancelled and a gone departure the client leaves out.
MAJORSTUEN_CALLS = [
    call("5", "Vestli", 7),
    call("19", "Ljabru", 3, mode="tram", quay="A"),
    call("3", "Mortensrud", 2),
    call("5", "Vestli", 12),
    call("2", "Ellingsrudåsen", 4, cancellation=True),
    call("4", "Bergkrystallen", -3),
]
# One quay of it, with its own code ("2") and its stop place's modes.
QUAY = {"id": QUAY_ID, "name": "Majorstuen", "publicCode": "2", "stopPlace": MAJORSTUEN}
QUAY_CALLS = [call("1", "Frognerseteren", 9), call("3", "Mortensrud", 2)]

# What mocked Entur knows: an id's stop (as the query's root field) and its calls. FAILING_ID answers 500.
STOPS = {MAJORSTUEN_ID: ("stopPlace", MAJORSTUEN, MAJORSTUEN_CALLS), QUAY_ID: ("quay", QUAY, QUAY_CALLS)}


def entur(request):
    """Answer a Journey Planner query for one of STOPS, as Entur would."""
    variables = json.loads(request.body)["variables"]
    stop_id = variables["id"]
    if stop_id == FAILING_ID:
        return 500, {}, json.dumps({"message": "Entur is having a bad day"})
    if stop_id not in STOPS:
        field = "quay" if stop_id.startswith("NSR:Quay:") else "stopPlace"
        return 200, {}, json.dumps({"data": {field: None}})
    field, stop, calls = STOPS[stop_id]
    return 200, {}, json.dumps({"data": {field: stop | {"estimatedCalls": calls[: variables["numberOfDepartures"]]}}})


def mock_entur() -> None:
    responses.add_callback(responses.POST, JOURNEY_PLANNER_URL, callback=entur, content_type="application/json")


def queries() -> list[tuple[str, bool]]:
    """Return every query asked of Entur as (stop id, whether it asked for departures), in order."""
    asked = []
    for c in responses.calls:
        variables = json.loads(c.request.body)["variables"]
        # The lookup asks for 1 call and no start time; departures from a start time.
        asked.append((variables["id"], "startTime" in variables))
    return asked


def make_provider() -> EnturProvider:
    # A plain session, so a 500 isn't retried.
    return EnturProvider(EnturSource(requests.Session()), now=lambda: NOW)


def fetched(stop_id: str) -> tuple[EnturStop, list[EnturDeparture]]:
    """Ask (mocked) Entur for a stop and its departures as of NOW, as the renderer's EnturSource does."""
    source = EnturSource(requests.Session())
    stop = source.resolve(stop_id)
    assert stop is not None
    return stop, source.departures(stop, now=NOW)


# --- Mapping ------------------------------------------------------------------------


def test_a_stop_place_maps_to_stop_departures_soonest_first():
    stop = EnturStop(id=MAJORSTUEN_ID, name="Majorstuen", transport_modes=[TransportMode.METRO, TransportMode.TRAM])
    departures = [
        EnturDeparture(
            line="5",
            destination="Vestli",
            mode=TransportMode.METRO,
            expected_departure_time=NOW + timedelta(minutes=7),
            time_to_station=420,
            quay_code="2",
        ),
        EnturDeparture(
            line="19",
            destination="Ljabru",
            mode=TransportMode.TRAM,
            expected_departure_time=NOW + timedelta(minutes=3),
            time_to_station=180,
            quay_code="A",
        ),
    ]
    assert to_stop_departures(stop, departures, NOW) == StopDepartures(
        fetched_at=NOW,
        stop_id=MAJORSTUEN_ID,
        name="Majorstuen",
        mode="metro",
        stop_letter=None,
        modes=["metro", "tram"],
        departures=[
            Departure(
                line="19", destination="Ljabru", expected_at=NOW + timedelta(minutes=3), platform="A", mode="tram"
            ),
            Departure(
                line="5", destination="Vestli", expected_at=NOW + timedelta(minutes=7), platform="2", mode="metro"
            ),
        ],
    )


def test_modes_are_stored_as_entur_names_them():
    stop = EnturStop(id=MAJORSTUEN_ID, name="Majorstuen", transport_modes=[TransportMode.METRO])
    stored = json.loads(to_stop_departures(stop, [], NOW).model_dump_json())
    assert (stored["mode"], stored["modes"]) == ("metro", ["metro"])


@responses.activate
def test_a_quay_keeps_its_code_as_the_stops_letter_and_its_stop_places_modes():
    mock_entur()
    stored = to_stop_departures(*fetched(QUAY_ID), NOW)
    assert (stored.stop_id, stored.name, stored.stop_letter, stored.mode, stored.modes) == (
        QUAY_ID,
        "Majorstuen",
        "2",
        "metro",
        ["metro", "tram"],
    )
    assert [(d.line, d.expected_at) for d in stored.departures] == [
        ("3", NOW + timedelta(minutes=2)),
        ("1", NOW + timedelta(minutes=9)),
    ]


def test_a_stop_listing_no_modes_is_unknown_and_nothing_due_still_maps():
    stored = to_stop_departures(EnturStop(id=UNKNOWN_ID, name="Somewhere"), [], NOW)
    assert (stored.mode, stored.modes, stored.departures) == ("unknown", [], [])


def test_a_value_stored_before_the_mode_fields_still_reads():
    # A TfL provider on the previous version wrote no `modes` or departure `mode`: read as none.
    old = {
        "fetched_at": "2026-10-17T12:00:00Z",
        "stop_id": "490000123W",
        "name": "Elephant & Castle",
        "mode": "bus",
        "stop_letter": "W",
        "departures": [{"line": "12", "destination": "Peckham", "expected_at": "2026-10-17T12:05:00Z"}],
    }
    stored = StopDepartures.model_validate_json(json.dumps(old))
    assert (stored.modes, stored.departures[0].mode, stored.departures[0].platform) == ([], None, None)


# --- Fetching -----------------------------------------------------------------------


@responses.activate
def test_fetch_returns_a_model_per_stop_keyed_by_its_id():
    mock_entur()
    provider = make_provider()

    results = asyncio.run(provider.fetch([MAJORSTUEN_ID, QUAY_ID]))

    assert set(results) == {MAJORSTUEN_ID, QUAY_ID}
    # Entur's client already left out the cancelled and the gone departures.
    assert [(d.line, d.mode) for d in results[MAJORSTUEN_ID].departures] == [
        ("3", "metro"),
        ("19", "tram"),
        ("5", "metro"),
        ("5", "metro"),
    ]
    assert all(r.fetched_at == NOW for r in results.values())
    assert provider.output_key(MAJORSTUEN_ID) == keys.departures(MAJORSTUEN_ID)


@responses.activate
def test_fetch_asks_as_the_renderer_does_named_and_for_as_many_departures():
    mock_entur()
    asyncio.run(make_provider().fetch([MAJORSTUEN_ID]))
    departures_query = json.loads(responses.calls[-1].request.body)["variables"]
    assert departures_query == {
        "id": MAJORSTUEN_ID,
        "numberOfDepartures": DEPARTURES_PER_STOP,
        "startTime": NOW.isoformat(),
    }
    assert all(c.request.headers["ET-Client-Name"] == HEADERS["ET-Client-Name"] for c in responses.calls)


@responses.activate
def test_fetch_leaves_out_unknown_and_failing_stops_without_losing_the_batch(caplog):
    mock_entur()

    results = asyncio.run(make_provider().fetch([UNKNOWN_ID, FAILING_ID, MAJORSTUEN_ID, QUAY_ID]))

    assert list(results) == [MAJORSTUEN_ID, QUAY_ID]
    assert f"Entur doesn't know stop {UNKNOWN_ID}" in caplog.text
    assert f"Couldn't fetch Entur stop {FAILING_ID}" in caplog.text


@responses.activate
def test_fetch_resolves_each_stop_only_once():
    mock_entur()
    provider = make_provider()
    asyncio.run(provider.fetch([MAJORSTUEN_ID]))
    asyncio.run(provider.fetch([MAJORSTUEN_ID]))
    assert queries() == [(MAJORSTUEN_ID, False), (MAJORSTUEN_ID, True), (MAJORSTUEN_ID, True)]


@responses.activate
def test_an_unknown_stop_is_asked_again_next_time():
    # It may have been added to Entur since.
    mock_entur()
    provider = make_provider()
    asyncio.run(provider.fetch([UNKNOWN_ID]))
    asyncio.run(provider.fetch([UNKNOWN_ID]))
    assert queries() == [(UNKNOWN_ID, False), (UNKNOWN_ID, False)]


# --- Through the runner -------------------------------------------------------------


@responses.activate
def test_runner_writes_the_stops_departures_under_its_key_with_the_ttl():
    mock_entur()
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    provider = make_provider()
    key = keys.departures(MAJORSTUEN_ID)

    async def cycle():
        await redis.sadd(keys.subscription(keys.ENTUR), MAJORSTUEN_ID, FAILING_ID)
        written = await ProviderRunner(provider, redis).run_cycle()
        return written, await redis.get(key), await redis.ttl(key), await redis.exists(keys.departures(FAILING_ID))

    written, value, ttl, failing_exists = asyncio.run(cycle())

    assert written == 1
    assert not failing_exists
    assert 0 < ttl <= provider.ttl_s
    # What the renderer reads back is what was fetched.
    stored = StopDepartures.model_validate_json(value)
    assert stored == to_stop_departures(*fetched(MAJORSTUEN_ID), NOW)
