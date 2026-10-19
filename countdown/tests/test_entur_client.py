import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from config_factory import make_config as make_broker_config
from test_utils import TEST_DEVICE_ID, put_departures

from countdown_api.models import DepartureStop
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.entur.entur_arrival_panel import EnturArrivalPanel
from countdown_core.entur.entur_client import (
    HEADERS,
    EnturClient,
    EnturDeparture,
    EnturStop,
    TransportMode,
    normalize_stop_id,
)
from countdown_core.store import keys
from countdown_core.tfl.stored_source import StoredArrivalPanel
from countdown_providers.entur.main import to_stop_departures

NOW = datetime(2026, 10, 16, 16, 0, tzinfo=UTC)


def _at(minutes: float, now: datetime = NOW) -> str:
    return (now + timedelta(minutes=minutes)).isoformat()


def _call(line: str, to: str, minutes: float, mode: str = "metro", now: datetime = NOW, **extra) -> dict:
    return {
        "realtime": True,
        "cancellation": False,
        "forBoarding": True,
        "aimedDepartureTime": _at(minutes, now),
        "expectedDepartureTime": _at(minutes, now),
        "destinationDisplay": {"frontText": to},
        "quay": {"publicCode": "2"},
        "serviceJourney": {"transportMode": mode, "line": {"publicCode": line, "name": to, "transportMode": mode}},
    } | extra


def _session(data: dict) -> MagicMock:
    session = MagicMock()
    session.post.return_value.json.return_value = {"data": data}
    return session


MAJORSTUEN = {"id": "NSR:StopPlace:58381", "name": "Majorstuen", "transportMode": ["metro", "tram"]}


def test_a_bare_number_is_a_stop_place():
    assert normalize_stop_id(58381) == "NSR:StopPlace:58381"
    assert normalize_stop_id("NSR:Quay:7333") == "NSR:Quay:7333"


def test_every_request_names_the_app_to_entur_without_touching_the_shared_session():
    session = _session({"stopPlace": MAJORSTUEN | {"estimatedCalls": []}})
    EnturClient(session).get_stop_info("NSR:StopPlace:58381")
    assert session.post.call_args.kwargs["headers"] == HEADERS
    session.headers.update.assert_not_called()


def test_a_stop_place_has_its_modes_and_no_code():
    stop = EnturClient(_session({"stopPlace": MAJORSTUEN | {"estimatedCalls": []}})).get_stop_info(58381)
    assert (stop.name, stop.public_code, stop.transport_modes) == (
        "Majorstuen",
        None,
        [TransportMode.METRO, TransportMode.TRAM],
    )


def test_a_quay_has_its_code_and_its_stop_places_modes():
    quay = {
        "id": "NSR:Quay:7333",
        "name": "Majorstuen",
        "publicCode": "2",
        "stopPlace": MAJORSTUEN,
        "estimatedCalls": [],
    }
    stop = EnturClient(_session({"quay": quay})).get_stop_info("NSR:Quay:7333")
    assert (stop.id, stop.public_code, stop.transport_modes[0]) == ("NSR:Quay:7333", "2", TransportMode.METRO)


def test_an_unknown_stop_is_none():
    assert EnturClient(_session({"stopPlace": None})).get_stop_info("NSR:StopPlace:1") is None


def test_an_entur_error_is_raised():
    session = MagicMock()
    session.post.return_value.json.return_value = {"errors": [{"message": "bad id"}]}
    with pytest.raises(RuntimeError, match="bad id"):
        EnturClient(session).get_stop_info("NSR:StopPlace:1")


def test_departures_leave_out_cancelled_terminating_and_gone_ones_soonest_first():
    calls = [
        _call("5", "Vestli", 7),
        _call("3", "Mortensrud", 2),
        _call("2", "Ellingsrudåsen", 4, cancellation=True),
        _call("1", "Frognerseteren", 5, forBoarding=False),
        _call("4", "Bergkrystallen", -3),
    ]
    departures = EnturClient(_session({"stopPlace": MAJORSTUEN | {"estimatedCalls": calls}})).get_departures(
        58381, now=NOW
    )
    assert [(d.line, d.destination, d.time_to_station) for d in departures] == [
        ("3", "Mortensrud", 120),
        ("5", "Vestli", 420),
    ]


def test_settings_from_an_older_broker_take_the_tfl_stops():
    config = make_broker_config(tfl={"stop_ids": ["940GZZLUKNG"], "style": "hero"})
    settings = DeparturesSettings.from_config(config)
    assert settings.stops == (DepartureStop(source="tfl", id="940GZZLUKNG"),)
    assert settings.style == "hero"


def test_settings_prefer_departures_with_each_stops_source():
    config = make_broker_config(
        tfl={"stop_ids": ["940GZZLUKNG"]},
        departures={
            "stops": [{"source": "entur", "id": "NSR:Quay:7333"}, {"source": "tfl", "id": "940GZZLUKNG"}],
            "style": "card",
        },
    )
    settings = DeparturesSettings.from_config(config)
    assert [(s.source, s.id) for s in settings.stops] == [("entur", "NSR:Quay:7333"), ("tfl", "940GZZLUKNG")]
    assert (settings.style, settings.stop_ids("tfl")) == ("card", ["940GZZLUKNG"])


@pytest.mark.parametrize(
    "sections",
    [
        {"tfl": {"stop_ids": ["940GZZLUKNG"], "style": "classic"}},
        {"tfl": {"stop_ids": []}, "departures": {"stops": [], "style": "classic"}},
    ],
    ids=["tfl", "departures"],
)
def test_the_retired_classic_style_is_drawn_as_hero(sections):
    """A broker that hasn't migrated its stored styles yet still sends classic."""
    assert DeparturesSettings.from_config(make_broker_config(**sections)).style == "hero"


def test_london_and_oslo_stops_share_one_panel_in_the_owners_order(store):
    """Both from the store, as the Entur and TfL providers write them."""
    now = datetime.now(UTC)
    majorstuen = EnturStop(id="NSR:StopPlace:58381", name="Majorstuen", transport_modes=[TransportMode.METRO])
    departure = EnturDeparture(
        line="5",
        destination="Vestli",
        mode=TransportMode.METRO,
        expected_departure_time=now + timedelta(minutes=3),
        time_to_station=180,
    )
    store.set(keys.departures(majorstuen.id), to_stop_departures(majorstuen, [departure], now).model_dump_json())
    put_departures(store, "490000123W", stop_letter="W", now=now)
    settings = DeparturesSettings(
        (DepartureStop(source="entur", id="NSR:StopPlace:58381"), DepartureStop(source="tfl", id="490000123W"))
    )
    client = DeparturesClient(TEST_DEVICE_ID, settings)

    panel = asyncio.run(client.update())

    oslo, london = panel.arrival_panels
    assert isinstance(oslo, EnturArrivalPanel)
    assert isinstance(london, StoredArrivalPanel)
    assert (oslo.stop_name, oslo.icon, oslo.departures[0].badge) == ("Majorstuen", "subway", "5")
    assert (london.stop_name, london.stop_letter) == ("Elephant & Castle", "W")


def test_a_stop_from_a_source_this_renderer_doesnt_know_is_skipped():
    client = DeparturesClient(TEST_DEVICE_ID, DeparturesSettings(()))
    unknown = DepartureStop.model_construct(source="vy", id="x")
    assert client.init_stops([unknown]) == []


def _departures(*modes: TransportMode) -> list[EnturDeparture]:
    return [
        EnturDeparture(
            line=str(i),
            destination="Somewhere",
            mode=mode,
            expected_departure_time=datetime(2026, 10, 16, 16, i, tzinfo=UTC),
            time_to_station=i * 60,
        )
        for i, mode in enumerate(modes, 1)
    ]


def test_a_stop_serving_several_modes_lists_each_in_its_header_and_marks_every_row():
    stop = EnturStop(id="x", name="Majorstuen", transport_modes=[TransportMode.METRO, TransportMode.TRAM])
    panel = EnturArrivalPanel(stop, _departures(TransportMode.TRAM, TransportMode.METRO))

    assert panel.icons == ["subway", "tram"]
    assert panel.icon == "subway"
    assert panel.mixed_modes
    assert [d.icon for d in panel.departures] == ["tram", "subway"]


def test_a_stop_serving_one_mode_marks_no_rows():
    stop = EnturStop(id="x", name="Frogner plass", transport_modes=[TransportMode.TRAM])
    panel = EnturArrivalPanel(stop, _departures(TransportMode.TRAM, TransportMode.TRAM))

    assert panel.icons == ["tram"]
    assert not panel.mixed_modes


def test_modes_drawn_alike_count_once_and_a_departures_own_mode_joins_the_stops():
    stop = EnturStop(id="x", name="Oslo S", transport_modes=[TransportMode.BUS])
    panel = EnturArrivalPanel(stop, _departures(TransportMode.COACH, TransportMode.RAIL))

    assert panel.icons == ["bus", "train"]
    assert panel.mixed_modes


def test_a_stop_listing_no_modes_and_no_departures_shows_a_bus():
    assert EnturArrivalPanel(EnturStop(id="x", name="Somewhere"), []).icons == ["bus"]
