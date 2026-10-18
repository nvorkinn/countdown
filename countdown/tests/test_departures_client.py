"""The departures client: the configured stops' departures, read from the store a page at a time."""

import asyncio

import pytest
from requests.adapters import HTTPAdapter
from test_utils import REGISTRATION, put_departures

from countdown_api.models import DepartureStop
from countdown_core.core.abstract_client import ClientStatus
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings, ResolvedStop
from countdown_core.entur.stored_source import StoredEnturSource
from countdown_core.store import reader
from countdown_core.tfl.combined_arrival_panel import Rotation
from countdown_core.tfl.models import MetroStopPoint, SingleStopPoint
from countdown_core.tfl.stored_source import StoredArrivalPanel, StoredTflSource, StoredTubePanel
from countdown_core.tfl.tfl_client import _find_stop_child

BUS_STOP_JSON = {
    "naptanId": "490000123W",
    "commonName": "Elephant & Castle",
    "modes": ["bus"],
    "additionalProperties": [],
    "children": [],
    "stopType": "NaptanPublicBusCoachTram",
    "stopLetter": "W",
}

METRO_STOP_JSON = {
    "naptanId": "940GZZLUKNG",
    "commonName": "Kennington Underground Station",
    "modes": ["tube"],
    "additionalProperties": [],
    "children": [],
    "stopType": "NaptanMetroStation",
}


def make_bus_stop(naptan_id="490000123W"):
    return SingleStopPoint.model_validate(dict(BUS_STOP_JSON, naptanId=naptan_id))


def make_metro_stop(naptan_id="940GZZLUKNG"):
    return MetroStopPoint.model_validate(dict(METRO_STOP_JSON, naptanId=naptan_id))


def make_config(stop_ids: list[str] | None = None, style: str = "hero") -> DeparturesSettings:
    stops = tuple(DepartureStop(source="tfl", id=stop_id) for stop_id in stop_ids or [])
    return DeparturesSettings(stops, style)


def tfl_stops(client: DeparturesClient, *stop_ids: str) -> list[ResolvedStop]:
    """Get the stops, as the client holds them once resolved: the stored source resolves a stop to its id."""
    return [ResolvedStop(stop_id, client.sources["tfl"], stop_id) for stop_id in stop_ids]


def stop_ids(count: int) -> list[str]:
    return [f"49000000{i}" for i in range(count)]


def test_find_stop_child_direct_match():
    stop = make_metro_stop()
    assert _find_stop_child(stop, stop.naptan_id) is stop


def test_find_stop_child_nested_match():
    child = make_bus_stop()
    parent = MetroStopPoint.model_validate(dict(METRO_STOP_JSON, children=[BUS_STOP_JSON]))
    found = _find_stop_child(parent, child.naptan_id)
    assert found is not None
    assert found.naptan_id == child.naptan_id


def test_find_stop_child_not_found_returns_none():
    stop = make_metro_stop()
    assert _find_stop_child(stop, "does-not-exist") is None


def test_construction_reads_nothing_and_reads_both_sources_from_the_store(store):
    client = DeparturesClient(REGISTRATION, make_config(["940GZZLUKNG"]))
    assert client.stops == []
    assert isinstance(client.sources["tfl"], StoredTflSource)
    assert isinstance(client.sources["entur"], StoredEnturSource)
    assert client.sources["tfl"].client is store


def test_without_a_store_it_cant_be_built_and_says_why(monkeypatch):
    """The registry turns that into a FailedClient, so the area shows its error state rather than vanishing."""
    monkeypatch.setattr(reader, "store", lambda: None)

    with pytest.raises(reader.StoreNotConfiguredError, match="REDIS_URL"):
        DeparturesClient(REGISTRATION, make_config(["940GZZLUKNG"]))


def test_initialise_resolves_stops_without_reading_the_store():
    """A stop the provider hasn't fetched yet is kept, so it's drawn once it has."""
    client = DeparturesClient(REGISTRATION, make_config(["940GZZLUKNG"]))

    asyncio.run(client.initialise())

    assert [stop.id for stop in client.stops] == ["940GZZLUKNG"]
    assert client.status == ClientStatus.CONNECTED


def test_initialise_leaves_stops_empty_when_none_configured():
    client = DeparturesClient(REGISTRATION, make_config())
    asyncio.run(client.initialise())
    assert client.stops == []


def test_init_stops_is_a_pure_function_returning_resolved_stops():
    client = DeparturesClient(REGISTRATION, make_config())

    result = client.init_stops(make_config(["940GZZLUKNG"]).stops)

    assert len(result) == 1
    assert client.stops == []  # init_stops does not mutate self


def test_the_page_is_the_minute_modulo_the_number_of_pages(rotation_clock):
    client = DeparturesClient(REGISTRATION, make_config())
    client.stops = tfl_stops(client, *stop_ids(5))
    client.stops_per_update = 2
    pages = []
    for minute in range(4):
        rotation_clock.at_minute(minute)
        pages.append(client._this_page())
    assert pages == [
        ([0, 1], Rotation(0, 3)),
        ([2, 3], Rotation(1, 3)),
        ([4], Rotation(2, 3)),  # the last page holds what's left
        ([0, 1], Rotation(0, 3)),
    ]


def test_the_same_minute_shows_the_same_page(rotation_clock):
    client = DeparturesClient(REGISTRATION, make_config())
    client.stops = tfl_stops(client, *stop_ids(5))
    client.stops_per_update = 2
    rotation_clock.at_minute(1)

    assert client._this_page() == client._this_page() == ([2, 3], Rotation(1, 3))


def test_the_page_follows_the_clock_when_there_are_fewer_pages_now(rotation_clock):
    client = DeparturesClient(REGISTRATION, make_config())
    client.stops = tfl_stops(client, *stop_ids(5))
    client.stops_per_update = 2
    rotation_clock.at_minute(5)
    assert client._this_page() == ([4], Rotation(2, 3))

    client.stops = client.stops[:4]  # a stop removed: two pages now, and minute 5 is the second

    assert client._this_page() == ([2, 3], Rotation(1, 2))


def test_a_client_rebuilt_by_a_config_change_carries_on_at_the_clocks_page(rotation_clock):
    """Not back to the first page: the page is the clock's, whichever client draws it."""
    rotation_clock.at_minute(1)
    before = DeparturesClient(REGISTRATION, make_config())
    before.stops = tfl_stops(before, *stop_ids(4))
    before.stops_per_update = 2

    rebuilt = DeparturesClient(REGISTRATION, make_config())
    rebuilt.stops = tfl_stops(rebuilt, *stop_ids(4))
    rebuilt.stops_per_update = 2

    assert before._this_page() == rebuilt._this_page() == ([2, 3], Rotation(1, 2))


def test_update_builds_correct_panels(store):
    put_departures(store, "490000123W", [("N155", "Somewhere", 300)], stop_letter="W")
    put_departures(
        store, "940GZZLUKNG", [("Northern", "Bank", 120)], name="Kennington Underground Station", mode="tube"
    )
    client = DeparturesClient(REGISTRATION, make_config(["490000123W", "940GZZLUKNG"]))

    panel = asyncio.run(client.update())

    bus, tube = panel.arrival_panels
    assert type(bus) is StoredArrivalPanel
    assert isinstance(tube, StoredTubePanel)
    assert (bus.stop_name, bus.stop_letter, tube.stop_name) == ("Elephant & Castle", "W", "Kennington")


def _shown_stop_ids(client: DeparturesClient) -> list[str]:
    return [panel.stored.stop_id for panel in asyncio.run(client.update()).arrival_panels]


def _nothing_due(store, client: DeparturesClient) -> None:
    for stop in client.stops:
        put_departures(store, stop.id)


def test_update_pages_through_more_stops_than_the_layout_shows(store, rotation_clock):
    client = DeparturesClient(REGISTRATION, make_config())
    client.stops = tfl_stops(client, *stop_ids(8))
    client.stops_per_update = 4
    _nothing_due(store, client)

    assert _shown_stop_ids(client) == stop_ids(4)
    rotation_clock.advance()
    assert _shown_stop_ids(client) == stop_ids(8)[4:]
    rotation_clock.advance()
    assert _shown_stop_ids(client) == stop_ids(4)


def test_update_shows_every_stop_in_order_when_they_all_fit(store):
    """No rotation, so no stop shown twice: 3 stops in a 4-stop layout are just 1, 2, 3."""
    client = DeparturesClient(REGISTRATION, make_config())
    client.stops = tfl_stops(client, *stop_ids(3))
    client.stops_per_update = 4
    _nothing_due(store, client)

    assert _shown_stop_ids(client) == ["490000000", "490000001", "490000002"]
    assert _shown_stop_ids(client) == ["490000000", "490000001", "490000002"]


def test_update_says_which_page_is_on_screen_when_they_rotate(store, rotation_clock):
    """With 3 stops and room for 2 there are two pages: 0+1, then 2 on its own."""
    client = DeparturesClient(REGISTRATION, make_config())
    client.stops = tfl_stops(client, *stop_ids(3))
    client.stops_per_update = 2
    _nothing_due(store, client)

    rotations = []
    for minute in range(3):
        rotation_clock.at_minute(minute)
        rotations.append(asyncio.run(client.update()).rotation)
    assert rotations == [Rotation(0, 2), Rotation(1, 2), Rotation(0, 2)]


def test_update_has_no_rotation_when_every_stop_fits(store):
    client = DeparturesClient(REGISTRATION, make_config())
    client.stops = tfl_stops(client, *stop_ids(2))
    client.stops_per_update = 2
    _nothing_due(store, client)

    assert asyncio.run(client.update()).rotation is None


def test_update_leaves_out_a_stop_the_store_has_nothing_for(store):
    put_departures(store, "490000000")
    client = DeparturesClient(REGISTRATION, make_config(stop_ids(2)))

    assert _shown_stop_ids(client) == ["490000000"]


def test_update_raises_when_no_stops_departures_are_in_the_store():
    """Raised rather than returning an empty panel, so the registry keeps the last good one."""
    client = DeparturesClient(REGISTRATION, make_config(["490000123W"]))

    with pytest.raises(RuntimeError):
        asyncio.run(client.update())


def test_update_raises_when_redis_cant_be_asked_so_the_last_panel_stays(store, monkeypatch):
    """A transient Redis outage is just a failed update: the registry keeps the last panel."""
    put_departures(store, "490000123W")
    client = DeparturesClient(REGISTRATION, make_config(["490000123W"]))

    def unreachable(*args, **kwargs):
        raise reader.redis.exceptions.ConnectionError

    monkeypatch.setattr(store, "get", unreachable)

    with pytest.raises(RuntimeError):
        asyncio.run(client.update())


def test_update_says_so_when_no_stops_are_configured():
    client = DeparturesClient(REGISTRATION, make_config([]))

    assert asyncio.run(client.update()).message == "Needs configuring"


def test_session_has_retry_adapter_mounted():
    """Every client keeps a retrying session, even though the departures and their notices now come from the store."""
    client = DeparturesClient(REGISTRATION, make_config())
    adapter = client.session.get_adapter("https://api.tfl.gov.uk")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.max_retries.total == 3


def test_the_configured_style_reaches_the_panels_with_every_departure_grouped_by_route(store):
    departures = [("36", "Queen's Park", 540), ("185", "Victoria", 240), ("36", "Queen's Park", 120)]
    put_departures(store, "490000123W", departures)
    client = DeparturesClient(REGISTRATION, make_config(["490000123W"], style="hero"))

    (panel,) = asyncio.run(client.update()).arrival_panels

    assert panel.style == "hero"
    assert [(d.badge, d.minutes) for d in panel.departures] == [("36", (2, 9)), ("185", (4,))]
