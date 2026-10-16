import asyncio
import json
import time
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses
from requests.adapters import HTTPAdapter
from test_utils import REGISTRATION

from countdown_api.models import Glowmarkt
from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, ClientStatus
from countdown_core.glow import glow_client
from countdown_core.glow.glow_client import GlowClient, _get_utc_offset

BASE_URL = GlowClient.base_url


@pytest.fixture(autouse=True)
def glowmarkt_api():
    """GlowClient authenticates and looks up its resource id in initialise(), so
    tests that call it need these two endpoints -- and anything *else* should fail
    loudly instead of reaching the real API (which rate-limits fake credentials
    with a 429).
    """
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.POST, f"{BASE_URL}/auth", json={"token": "initial-token"})
        rsps.add(
            responses.GET,
            f"{BASE_URL}/virtualentity",
            json=[{"resources": [{"name": "electricity consumption", "resourceId": "elec-id"}]}],
        )
        yield rsps


def make_client() -> GlowClient:
    return GlowClient(REGISTRATION, Glowmarkt(username="me@example.com", password="hunter2"))


def make_initialised_client() -> GlowClient:
    client = make_client()
    asyncio.run(client.initialise())
    return client


def test_construction_makes_no_requests(glowmarkt_api):
    client = make_client()

    assert client.token is None
    assert client.resource_id is None
    assert len(glowmarkt_api.calls) == 0


def test_initialise_authenticates_and_resolves_the_electricity_resource():
    client = make_initialised_client()

    assert client.token == "initial-token"
    assert client.resource_id == "elec-id"
    assert not client.is_disabled()


@pytest.mark.parametrize(
    ("utc_offset", "expected"),
    [
        (timedelta(0), "0"),
        (timedelta(hours=1), "-60"),
        (timedelta(hours=-5), "300"),
        (timedelta(hours=5, minutes=30), "-330"),
        (timedelta(hours=-3, minutes=-30), "210"),
    ],
)
def test_get_utc_offset_is_minutes_from_local_to_utc(utc_offset, expected):
    now = datetime(2026, 1, 1, tzinfo=timezone(utc_offset))
    assert _get_utc_offset(now) == expected


def test_get_utc_offset_reads_a_naive_time_in_the_machine_zone(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert _get_utc_offset(datetime(2026, 1, 1)) == "300"
        assert _get_utc_offset(datetime(2026, 7, 1)) == "240"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_authenticate_stores_token(monkeypatch):
    client = make_client()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"token": "abc123"}
    monkeypatch.setattr(client.session, "post", MagicMock(return_value=fake_response))

    client._authenticate()

    assert client.token == "abc123"


def test_authenticate_passes_a_timeout(monkeypatch):
    client = make_client()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"token": "abc123"}
    post_mock = MagicMock(return_value=fake_response)
    monkeypatch.setattr(client.session, "post", post_mock)

    client._authenticate()

    assert post_mock.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


def test_request_authenticates_once_when_it_has_no_token(monkeypatch):
    client = make_client()
    client.token = None
    auth_response = MagicMock()
    auth_response.raise_for_status = MagicMock()
    auth_response.json.return_value = {"token": "abc123"}
    post_mock = MagicMock(return_value=auth_response)
    monkeypatch.setattr(client.session, "post", post_mock)

    data_response = MagicMock()
    data_response.status_code = 200
    data_response.raise_for_status = MagicMock()
    data_response.json.return_value = {"ok": True}
    request_mock = MagicMock(return_value=data_response)
    monkeypatch.setattr(client.session, "request", request_mock)

    result = client._request("GET", "/some/endpoint")

    assert result == {"ok": True}
    post_mock.assert_called_once()
    assert request_mock.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


def test_request_reauthenticates_on_401(monkeypatch):
    client = make_client()
    client.token = "stale-token"

    auth_response = MagicMock()
    auth_response.raise_for_status = MagicMock()
    auth_response.json.return_value = {"token": "fresh-token"}
    monkeypatch.setattr(client.session, "post", MagicMock(return_value=auth_response))

    unauthorized_response = MagicMock()
    unauthorized_response.status_code = 401
    unauthorized_response.raise_for_status = MagicMock()

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.raise_for_status = MagicMock()
    ok_response.json.return_value = {"ok": True}

    request_mock = MagicMock(side_effect=[unauthorized_response, ok_response])
    monkeypatch.setattr(client.session, "request", request_mock)

    result = client._request("GET", "/some/endpoint")

    assert result == {"ok": True}
    assert client.token == "fresh-token"
    assert request_mock.call_count == 2


def test_session_has_retry_adapter_mounted():
    client = make_client()
    adapter = client.session.get_adapter("https://api.glowmarkt.com")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.max_retries.total == 3


def test_get_electricity_resource_id_finds_electricity_entry(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        MagicMock(
            return_value=[
                {
                    "resources": [
                        {"name": "gas consumption", "resourceId": "gas-id"},
                        {"name": "electricity consumption", "resourceId": "elec-id"},
                    ]
                }
            ]
        ),
    )

    assert client.get_electricity_resource_id() == "elec-id"


def test_get_electricity_resource_id_raises_when_missing(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        MagicMock(return_value=[{"resources": [{"name": "gas consumption", "resourceId": "gas-id"}]}]),
    )

    with pytest.raises(ValueError):
        client.get_electricity_resource_id()


def test_get_readings_parses_epoch_and_iso_timestamps(glowmarkt_api):
    glowmarkt_api.add(
        responses.GET,
        f"{BASE_URL}/resource/elec-id/readings",
        json={"data": [[1700000000, 1.5], ["2023-11-14T23:00:00", 2.25]]},
    )
    client = make_initialised_client()

    readings = client._get_readings(datetime(2023, 11, 14, tzinfo=UTC), datetime(2023, 11, 15, tzinfo=UTC), "PT1H")

    assert readings == [
        (datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC), 1.5),
        (datetime(2023, 11, 14, 23, 0, tzinfo=UTC), 2.25),
    ]
    request = glowmarkt_api.calls[-1].request
    assert "period=PT1H" in request.url
    assert request.headers["token"] == "initial-token"


def test_get_readings_labels_buckets_as_local_wall_clock(glowmarkt_api):
    """Glow encodes each bucket's local wall-clock start as if it were UTC: with
    offset=-60, the 06:00 BST bucket comes back as the epoch for 06:00Z.
    """
    bst = timezone(timedelta(hours=1), "BST")
    glowmarkt_api.add(
        responses.GET,
        f"{BASE_URL}/resource/elec-id/readings",
        json={"data": [[int(datetime(2026, 9, 25, 6, tzinfo=UTC).timestamp()), 1.5], ["2026-09-25T07:00:00", 2.25]]},
    )
    client = make_initialised_client()

    readings = client._get_readings(datetime(2026, 9, 25, 6, tzinfo=bst), datetime(2026, 9, 25, 8, tzinfo=bst), "PT1H")

    assert readings == [(datetime(2026, 9, 25, 6, tzinfo=bst), 1.5), (datetime(2026, 9, 25, 7, tzinfo=bst), 2.25)]


def _fake_glow_readings(request):
    """Mimic Glow: one bucket per period from `from` to `to` (local wall clock),
    each labelled with its wall-clock start encoded as a UTC epoch.
    """
    params = {k: v[0] for k, v in parse_qs(urlparse(request.url).query).items()}
    start, end = datetime.fromisoformat(params["from"]), datetime.fromisoformat(params["to"])
    period = params["period"]
    if period == "PT1H":
        cursor = start.replace(minute=0, second=0)
    elif period == "P1D":
        cursor = start.replace(hour=0, minute=0, second=0)
    else:
        cursor = start.replace(day=1, hour=0, minute=0, second=0)
    data = []
    while cursor <= end:
        data.append([int(cursor.replace(tzinfo=UTC).timestamp()), 1.0])
        if period == "PT1H":
            cursor += timedelta(hours=1)
        elif period == "P1D":
            cursor += timedelta(days=1)
        else:
            cursor = cursor.replace(year=cursor.year + cursor.month // 12, month=cursor.month % 12 + 1)
    return 200, {}, json.dumps({"data": data})


BST = timezone(timedelta(hours=1), "BST")


@pytest.fixture
def clock(monkeypatch):
    """Freezes GlowClient's idea of now; tests move it by reassigning clock.now.
    Also puts the process in UK time, since _update() converts now to the
    machine's zone - otherwise a UTC CI runner never exercises BST.
    """
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()

    class Clock:
        now = datetime(2026, 9, 25, 6, 10, tzinfo=BST)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return Clock.now

    monkeypatch.setattr(glow_client, "datetime", FrozenDatetime)
    yield Clock
    monkeypatch.undo()
    time.tzset()


def _readings_requests(glowmarkt_api) -> list[dict[str, list[str]]]:
    return [parse_qs(urlparse(c.request.url).query) for c in glowmarkt_api.calls if "/readings" in c.request.url]


def _update_page(client: GlowClient, page_index: int):
    client.page_index = page_index
    return asyncio.run(client.update())


@pytest.mark.parametrize("page_index", [0, 1])
def test_refresh_after_the_first_only_fetches_the_open_bucket(glowmarkt_api, clock, page_index):
    """The regression this guards: cached bucket keys were an hour off the bucket
    starts _update() walks during BST, so every refresh re-fetched the whole window.
    """
    glowmarkt_api.add_callback(responses.GET, f"{BASE_URL}/resource/elec-id/readings", callback=_fake_glow_readings)
    client = make_initialised_client()

    _update_page(client, page_index)
    clock.now += GlowClient.refresh_intervals[page_index]
    _update_page(client, page_index)

    requests_made = _readings_requests(glowmarkt_api)
    assert len(requests_made) == 2
    open_start = "2026-09-25T06:00:00" if page_index == 0 else "2026-09-25T00:00:00"
    assert requests_made[1]["from"] == [open_start]


@pytest.mark.parametrize("page_index", [0, 1, 2])
def test_page_is_redrawn_from_cache_until_its_refresh_interval_passes(glowmarkt_api, clock, page_index):
    glowmarkt_api.add_callback(responses.GET, f"{BASE_URL}/resource/elec-id/readings", callback=_fake_glow_readings)
    client = make_initialised_client()
    first = _update_page(client, page_index)

    clock.now += GlowClient.refresh_intervals[page_index] - timedelta(minutes=1)
    cached = _update_page(client, page_index)
    assert len(_readings_requests(glowmarkt_api)) == 1
    assert cached.readings == first.readings

    clock.now += timedelta(minutes=1)
    _update_page(client, page_index)
    assert len(_readings_requests(glowmarkt_api)) == 2


def test_update_rotates_pages_every_call_without_refetching(glowmarkt_api, clock):
    glowmarkt_api.add_callback(responses.GET, f"{BASE_URL}/resource/elec-id/readings", callback=_fake_glow_readings)
    client = make_initialised_client()

    pages = []
    for _ in range(6):
        pages.append(asyncio.run(client.update()).page_index)
        clock.now += timedelta(minutes=1)

    assert pages == [0, 1, 2, 0, 1, 2]
    assert len(_readings_requests(glowmarkt_api)) == 3


def test_update_returns_a_panel_and_rotates_through_the_three_pages(monkeypatch):
    client = make_client()
    monkeypatch.setattr(client, "_get_readings", MagicMock(return_value=[]))

    pages = [asyncio.run(client.update()).page_index for _ in range(4)]

    assert pages == [0, 1, 2, 0]


def test_glowmarkt_being_down_at_boot_heals_on_a_later_update(glowmarkt_api, monkeypatch):
    """The regression this guards: initialise() failing once used to leave resource_id
    unset forever, so every later poll asked for readings of resource "None".
    """
    glowmarkt_api.replace(responses.POST, f"{BASE_URL}/auth", status=503)
    client = make_client()
    with pytest.raises(requests.exceptions.RequestException):
        asyncio.run(client.initialise())
    assert client.status == ClientStatus.ERROR
    assert client.resource_id is None

    glowmarkt_api.replace(responses.POST, f"{BASE_URL}/auth", json={"token": "recovered"})
    monkeypatch.setattr(client, "_get_readings", MagicMock(return_value=[]))
    asyncio.run(client.update())

    assert client.status == ClientStatus.CONNECTED
    assert client.token == "recovered"
    assert client.resource_id == "elec-id"
