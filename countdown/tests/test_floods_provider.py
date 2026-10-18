"""The floods provider against mocked Environment Agency and postcodes.io (responses), and what it stores.

fixtures/floods.json is one cycle: the EA's warnings in force, each postcode's place and flood areas, and the value
stored for it, which test_floods_store.py reads back.
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
import requests
import responses
from responses import matchers

from countdown_core.notices.location import POSTCODES_URL
from countdown_core.store import keys
from countdown_providers.floods.main import FLOOD_AREAS_URL, FLOODS_URL, FloodsProvider, district
from countdown_providers.runner import ProviderRunner

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "floods.json").read_text())
PLACES = FIXTURE["places"]
POSTCODES = list(PLACES)


class Clock:
    """The provider's now: the fixture's fetched_at, until a test moves it on."""

    def __init__(self) -> None:
        self.now = datetime.fromisoformat(FIXTURE["fetched_at"])

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


def add_place(api, postcode: str) -> None:
    """Answer postcodes.io and the EA's flood areas for `postcode`, as the fixture has them."""
    place = PLACES[postcode]
    found = place["postcodes_io"]["result"]
    api.add(responses.GET, f"{POSTCODES_URL}/{postcode.replace(' ', '%20')}", json=place["postcodes_io"])
    api.add(
        responses.GET,
        FLOOD_AREAS_URL,
        json=place["flood_areas"],
        match=[
            matchers.query_param_matcher(
                {"lat": str(found["latitude"]), "long": str(found["longitude"]), "dist": "5", "_limit": "2000"}
            )
        ],
    )


def add_in_force(api, **kwargs) -> None:
    api.add(responses.GET, FLOODS_URL, **(kwargs or {"json": FIXTURE["in_force"]}))


def calls_to(api, url: str) -> int:
    return sum(urlparse(call.request.url)._replace(query="").geturl() == url for call in api.calls)


def calls_starting(api, url: str) -> int:
    return sum(call.request.url.startswith(url) for call in api.calls)


def fetch(provider: FloodsProvider, ids: list[str]) -> dict:
    return asyncio.run(provider.fetch(ids))


def make_provider(clock) -> FloodsProvider:
    return FloodsProvider(requests.Session(), now=clock)


def test_each_postcode_gets_the_eas_warnings_for_the_flood_areas_near_it(api, clock):
    add_in_force(api)
    for postcode in POSTCODES:
        add_place(api, postcode)

    results = fetch(make_provider(clock), POSTCODES)

    assert list(results) == POSTCODES  # keyed by postcode, not the Redis key: the runner adds that
    for postcode in POSTCODES:
        assert json.loads(results[postcode].model_dump_json()) == PLACES[postcode]["stored"]


def test_one_list_of_the_warnings_in_force_serves_every_postcode(api, clock):
    add_in_force(api)
    for postcode in POSTCODES:
        add_place(api, postcode)
    provider = make_provider(clock)

    fetch(provider, POSTCODES[:2])  # one batch...
    fetch(provider, POSTCODES[2:])  # ...and the next, in the same cycle

    assert calls_to(api, FLOODS_URL) == 1
    # Asked for the whole of England, not filtered: the EA caches that request, and asks callers to keep to such.
    assert next(call.request.url for call in api.calls if call.request.url.startswith(FLOODS_URL)) == FLOODS_URL


def test_the_next_cycle_asks_for_the_warnings_in_force_again(api, clock):
    add_in_force(api)
    add_place(api, "SE17 3LL")
    provider = make_provider(clock)

    fetch(provider, ["SE17 3LL"])
    clock.now += timedelta(seconds=provider.interval_s)
    second = fetch(provider, ["SE17 3LL"])

    assert calls_to(api, FLOODS_URL) == 2
    assert second["SE17 3LL"].fetched_at == clock.now


def test_a_postcode_is_placed_once_and_its_flood_areas_asked_for_once_a_day(api, clock):
    add_in_force(api)
    add_place(api, "SE17 3LL")
    provider = make_provider(clock)

    for _ in range(3):
        fetch(provider, ["SE17 3LL"])
        clock.now += timedelta(hours=6)
    assert calls_starting(api, POSTCODES_URL) == 1
    assert calls_to(api, FLOOD_AREAS_URL) == 1

    clock.now += timedelta(hours=6)  # a day after the first
    fetch(provider, ["SE17 3LL"])
    assert calls_starting(api, POSTCODES_URL) == 1  # a postcode's place doesn't change
    assert calls_to(api, FLOOD_AREAS_URL) == 2  # but the EA redraws its flood areas now and then


def test_norwegian_postcodes_are_skipped(api, clock):
    """They're in v1:sub:postcode for Norway's notices; nothing is asked about them."""
    results = fetch(make_provider(clock), ["0150", "9008"])

    assert results == {}
    assert len(api.calls) == 0


def test_norwegian_postcodes_in_a_batch_with_uk_ones_are_left_out(api, clock):
    add_in_force(api)
    add_place(api, "YO1 7HH")

    results = fetch(make_provider(clock), ["0150", "YO1 7HH"])

    assert list(results) == ["YO1 7HH"]


def test_a_postcode_postcodes_io_doesnt_know_is_left_out_and_looked_up_again_a_day_later(api, clock, caplog):
    add_in_force(api)
    api.add(responses.GET, f"{POSTCODES_URL}/ZZ1%201ZZ", status=404, json={"status": 404, "error": "Invalid postcode"})
    provider = make_provider(clock)

    assert fetch(provider, ["ZZ1 1ZZ"]) == {}
    clock.now += timedelta(hours=1)
    assert fetch(provider, ["ZZ1 1ZZ"]) == {}
    assert calls_starting(api, POSTCODES_URL) == 1
    clock.now += timedelta(days=1)
    assert fetch(provider, ["ZZ1 1ZZ"]) == {}
    assert calls_starting(api, POSTCODES_URL) == 2

    assert calls_to(api, FLOOD_AREAS_URL) == 0
    assert "postcodes.io doesn't know a postcode in ZZ1; no floods for it" in caplog.messages
    assert "ZZ1 1ZZ" not in caplog.text


def test_one_postcode_that_cant_be_placed_doesnt_lose_the_rest_of_the_batch(api, clock, caplog):
    add_in_force(api)
    api.add(responses.GET, f"{POSTCODES_URL}/SE17%203LL", status=500)
    add_place(api, "YO1 7HH")

    results = fetch(make_provider(clock), ["SE17 3LL", "YO1 7HH"])

    assert list(results) == ["YO1 7HH"]
    # The district, not the postcode, nor the HTTP error's text (which quotes the URL, postcode and all).
    assert "Couldn't place a postcode in SE17: HTTPError" in caplog.messages
    assert "3LL" not in caplog.text


def test_flood_areas_the_ea_cant_give_leave_the_postcode_out_until_they_can(api, clock):
    add_in_force(api)
    found = PLACES["SE17 3LL"]["postcodes_io"]
    api.add(responses.GET, f"{POSTCODES_URL}/SE17%203LL", json=found)
    api.add(responses.GET, FLOOD_AREAS_URL, status=500)
    provider = make_provider(clock)

    assert fetch(provider, ["SE17 3LL"]) == {}

    api.replace(responses.GET, FLOOD_AREAS_URL, json=PLACES["SE17 3LL"]["flood_areas"])
    clock.now += timedelta(seconds=provider.interval_s)
    assert list(fetch(provider, ["SE17 3LL"])) == ["SE17 3LL"]
    assert calls_starting(api, POSTCODES_URL) == 1  # the place was kept


def test_the_ea_being_down_writes_nothing_without_naming_a_postcode(api, clock, caplog):
    add_in_force(api, status=503)
    provider = make_provider(clock)

    assert fetch(provider, ["SE17 3LL", "YO1 7HH"]) == {}
    assert fetch(provider, ["CF10 1EP"]) == {}  # the cycle's next batch doesn't ask again

    assert calls_to(api, FLOODS_URL) == 1
    assert calls_starting(api, POSTCODES_URL) == 0
    assert any(message.startswith("Couldn't fetch the flood warnings in force") for message in caplog.messages)
    assert not any(district(postcode) in caplog.text for postcode in POSTCODES)


@pytest.mark.parametrize(("postcode", "expected"), [("SE17 3LL", "SE17"), ("YO1 7HH", "YO1"), ("sw1a1aa", "SW1A")])
def test_a_postcode_is_logged_by_its_district(postcode, expected):
    assert district(postcode) == expected


def test_the_runner_writes_each_place_under_its_key_with_the_ttl(api, clock):
    add_in_force(api)
    for postcode in POSTCODES:
        add_place(api, postcode)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    provider = make_provider(clock)

    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), *POSTCODES, "0150")
        written = await ProviderRunner(provider, redis).run_cycle()
        values = {postcode: await redis.get(f"v1:notices:floods:{postcode.replace(' ', '')}") for postcode in POSTCODES}
        ttls = [await redis.ttl(keys.floods(postcode)) for postcode in POSTCODES]
        return written, values, ttls, await redis.keys("v1:notices:floods:*")

    written, values, ttls, written_keys = asyncio.run(cycle())

    assert written == 3
    assert {postcode: json.loads(value) for postcode, value in values.items()} == {
        postcode: PLACES[postcode]["stored"] for postcode in POSTCODES
    }
    assert all(0 < ttl <= provider.ttl_s for ttl in ttls)
    assert "v1:notices:floods:0150" not in written_keys
    assert provider.ttl_s > provider.interval_s


def test_the_flood_areas_are_asked_for_around_the_postcodes_place(api, clock):
    add_in_force(api)
    add_place(api, "YO1 7HH")

    fetch(make_provider(clock), ["YO1 7HH"])

    [call] = [call for call in api.calls if call.request.url.startswith(FLOOD_AREAS_URL)]
    assert parse_qs(urlparse(call.request.url).query) == {
        "lat": ["53.96197"],
        "long": ["-1.08273"],
        "dist": ["5"],
        "_limit": ["2000"],
    }
