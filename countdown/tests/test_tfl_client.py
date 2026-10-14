import asyncio
from unittest.mock import MagicMock

import pytest
from requests.adapters import HTTPAdapter
from test_utils import REGISTRATION

from countdown_api.models import Tfl
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, ClientStatus
from countdown_core.tfl.bus_arrival_panel import BusArrivalPanel
from countdown_core.tfl.models import MetroStopPoint, SingleStopPoint
from countdown_core.tfl.tfl_client import TflClient, _find_stop_child
from countdown_core.tfl.tube_arrival_panel import TubeArrivalPanel

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


def make_config(stop_ids: list[str] | None = None, app_key: str = "") -> Tfl:
    return Tfl(stop_ids=stop_ids or [], app_key=app_key)


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


def test_construction_never_touches_the_network():
    """__init__ must be cheap and unable to fail -- stop resolution is deferred to
    first use, so a flaky TfL API can never prevent a TflClient from being built."""
    client = TflClient(REGISTRATION, make_config(["940GZZLUKNG"], "app-key"))
    assert client.stops == []
    assert client.params == {"app_key": "app-key"}


def test_initialise_resolves_stops(monkeypatch):
    client = TflClient(REGISTRATION, make_config(["940GZZLUKNG"]))
    get_mock = MagicMock()
    get_mock.return_value.raise_for_status = MagicMock()
    get_mock.return_value.json.return_value = METRO_STOP_JSON
    monkeypatch.setattr(client.session, "get", get_mock)

    asyncio.run(client.initialise())

    assert len(client.stops) == 1
    get_mock.assert_called_once()


def test_initialise_leaves_stops_empty_when_none_configured():
    client = TflClient(REGISTRATION, make_config())
    asyncio.run(client.initialise())
    assert client.stops == []


def test_initialise_is_not_repeated_once_stops_are_populated(monkeypatch):
    client = TflClient(REGISTRATION, make_config(["940GZZLUKNG"]))
    get_mock = MagicMock()
    get_mock.return_value.raise_for_status = MagicMock()
    get_mock.return_value.json.return_value = METRO_STOP_JSON
    monkeypatch.setattr(client.session, "get", get_mock)

    asyncio.run(client.initialise())
    asyncio.run(client.initialise())

    get_mock.assert_called_once()


def test_failed_setup_leaves_stops_empty_and_is_retried_on_next_update(monkeypatch):
    client = TflClient(REGISTRATION, make_config(["940GZZLUKNG"]))
    get_mock = MagicMock(side_effect=ConnectionError("network is down"))
    monkeypatch.setattr(client.session, "get", get_mock)

    # TfL being down at boot mustn't leave the device without arrivals until it's restarted.
    with pytest.raises(RuntimeError):
        asyncio.run(client.update())
    assert client.stops == []
    assert client.status == ClientStatus.ERROR

    get_mock.side_effect = None
    get_mock.return_value.raise_for_status = MagicMock()
    get_mock.return_value.json.side_effect = [METRO_STOP_JSON, []]  # the stop, then its arrivals

    asyncio.run(client.update())
    assert len(client.stops) == 1
    assert client.status == ClientStatus.CONNECTED


def test_initialise_carries_on_when_only_some_stops_resolve(monkeypatch):
    client = TflClient(REGISTRATION, make_config(["940GZZLUKNG", "unknown-stop"]))

    def fake_get(url, params=None, timeout=None):
        if "940GZZLUKNG" not in url:
            raise ConnectionError("no such stop")
        response = MagicMock()
        response.json.return_value = METRO_STOP_JSON
        return response

    monkeypatch.setattr(client.session, "get", fake_get)

    asyncio.run(client.initialise())

    assert [stop.naptan_id for stop in client.stops] == ["940GZZLUKNG"]
    assert client.status == ClientStatus.CONNECTED


def test_init_stops_is_a_pure_function_returning_resolved_stops(monkeypatch):
    client = TflClient(REGISTRATION, make_config())
    get_mock = MagicMock()
    get_mock.return_value.raise_for_status = MagicMock()
    get_mock.return_value.json.return_value = METRO_STOP_JSON
    monkeypatch.setattr(client.session, "get", get_mock)

    result = client.init_stops(["940GZZLUKNG"])

    assert len(result) == 1
    assert client.stops == []  # init_stops does not mutate self


def test_get_next_stop_cycles_and_wraps():
    client = TflClient(REGISTRATION, make_config())
    a, b = make_bus_stop("A"), make_metro_stop("B")
    client.stops = [a, b]
    assert client._get_next_stop() is a
    assert client._get_next_stop() is b
    assert client._get_next_stop() is a


def test_update_builds_correct_panels(monkeypatch):
    client = TflClient(REGISTRATION, make_config())
    client.stops = [make_bus_stop(), make_metro_stop()]

    def fake_get(url, params=None, timeout=None):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "490000123W" in url:
            response.json.return_value = [
                {
                    "naptanId": "490000123W",
                    "lineName": "N155",
                    "timeToStation": 300,
                    "modeName": "bus",
                    "destinationName": "Somewhere",
                }
            ]
        else:
            response.json.return_value = [
                {
                    "naptanId": "940GZZLUKNG",
                    "lineName": "Northern",
                    "timeToStation": 120,
                    "modeName": "tube",
                    "towards": "Bank",
                    "destinationNaptanId": None,
                }
            ]
        return response

    monkeypatch.setattr(client.session, "get", fake_get)

    panel = asyncio.run(client.update())

    assert len(panel.arrival_panels) == 2
    assert isinstance(panel.arrival_panels[0], BusArrivalPanel)
    assert isinstance(panel.arrival_panels[1], TubeArrivalPanel)


def _shown_stop_ids(client: TflClient) -> list[str]:
    return [panel.stop.naptan_id for panel in asyncio.run(client.update()).arrival_panels]


def _no_arrivals(monkeypatch, client: TflClient) -> None:
    response = MagicMock(raise_for_status=MagicMock())
    response.json.return_value = []
    monkeypatch.setattr(client.session, "get", lambda url, params=None, timeout=None: response)


def test_update_pages_through_more_stops_than_the_layout_shows(monkeypatch):
    client = TflClient(REGISTRATION, make_config())
    client.stops = [make_bus_stop(f"49000000{i}") for i in range(8)]
    client.stops_per_update = 4
    _no_arrivals(monkeypatch, client)

    assert _shown_stop_ids(client) == [f"49000000{i}" for i in range(4)]
    assert _shown_stop_ids(client) == [f"49000000{i}" for i in range(4, 8)]
    assert _shown_stop_ids(client) == [f"49000000{i}" for i in range(4)]


def test_update_shows_every_stop_in_order_when_they_all_fit(monkeypatch):
    """No rotation, so no stop shown twice: 3 stops in a 4-stop layout are just 1, 2, 3."""
    client = TflClient(REGISTRATION, make_config())
    client.stops = [make_bus_stop(f"49000000{i}") for i in range(3)]
    client.stops_per_update = 4
    _no_arrivals(monkeypatch, client)

    assert _shown_stop_ids(client) == ["490000000", "490000001", "490000002"]
    assert _shown_stop_ids(client) == ["490000000", "490000001", "490000002"]


def test_update_raises_when_no_stops_arrivals_could_be_fetched(monkeypatch):
    """Raised rather than returning an empty panel, so the registry keeps the last good one."""
    client = TflClient(REGISTRATION, make_config())
    client.stops = [make_bus_stop()]

    def failing_get(url, params=None, timeout=None):
        raise ConnectionError("network is down")

    monkeypatch.setattr(client.session, "get", failing_get)

    with pytest.raises(RuntimeError):
        asyncio.run(client.update())


def test_update_says_so_when_no_stops_are_configured():
    client = TflClient(REGISTRATION, make_config([]))

    assert asyncio.run(client.update()).message == "Needs configuring"


def test_update_passes_a_timeout(monkeypatch):
    client = TflClient(REGISTRATION, make_config())
    client.stops = [make_bus_stop()]

    get_mock = MagicMock()
    get_mock.return_value.raise_for_status = MagicMock()
    get_mock.return_value.json.return_value = [
        {
            "naptanId": "490000123W",
            "lineName": "N155",
            "timeToStation": 300,
            "modeName": "bus",
            "destinationName": "Somewhere",
        }
    ]
    monkeypatch.setattr(client.session, "get", get_mock)

    asyncio.run(client.update())

    assert get_mock.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


def test_get_stop_info_passes_a_timeout(monkeypatch):
    client = TflClient(REGISTRATION, make_config())

    get_mock = MagicMock()
    get_mock.return_value.raise_for_status = MagicMock()
    get_mock.return_value.json.return_value = METRO_STOP_JSON
    monkeypatch.setattr(client.session, "get", get_mock)

    client._get_stop_info("940GZZLUKNG")

    assert get_mock.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


def test_session_has_retry_adapter_mounted():
    client = TflClient(REGISTRATION, make_config())
    adapter = client.session.get_adapter("https://api.tfl.gov.uk")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.max_retries.total == 3
