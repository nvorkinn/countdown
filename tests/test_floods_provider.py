"""The floods provider against a mocked Environment Agency (responses), and what it stores.

Where each postcode is comes from the store, as provider-locations writes it (fakeredis): nothing here asks
postcodes.io. fixtures/floods.json is one cycle: the EA's warnings in force, each postcode's place (postcodes.io's
answer, which provider-locations stores) and flood areas, and the value stored for it, which test_floods_store.py
reads back.
"""

import asyncio
import json
import logging
import socket
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses
from location_factory import FakeLocations
from responses import matchers

from countdown_core.core import log_format
from countdown_core.core.http_session import retrying_session
from countdown_core.notices.models import FloodWarningsResponse
from countdown_core.store import keys
from countdown_core.tfl.models import Postcode, PostcodeResponse
from countdown_providers.floods import main as floods_main
from countdown_providers.floods.main import FLOOD_AREAS_URL, FLOODS_URL, FloodsProvider
from countdown_providers.runner import ProviderRunner

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "floods.json").read_text())
PLACES = FIXTURE["places"]
POSTCODES = list(PLACES)
UNKNOWN = "ZZ1 1ZZ"


def place_of(postcode: str) -> Postcode:
    """Return where the fixture has `postcode`, as provider-locations stores it (from postcodes.io's answer)."""
    return PostcodeResponse.model_validate(PLACES[postcode]["postcodes_io"]).result


def traces(postcode: str) -> list[str]:
    """Return what the logs mustn't hold about `postcode`: it, its place, its district, and its coordinates."""
    compact = postcode.replace(" ", "").upper()
    found = PLACES.get(postcode, {}).get("postcodes_io", {}).get("result", {})
    about = [str(found[k]) for k in ("latitude", "longitude", "admin_district") if found.get(k)]
    return [postcode.upper(), compact, compact[:-3], *(t.upper() for t in about)]


def assert_no_postcode_in(text: str, postcodes: list[str]) -> None:
    for postcode in postcodes:
        for trace in traces(postcode):
            assert trace not in text.upper(), f"the logs mention {trace!r} ({postcode})"


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


@pytest.fixture
def located():
    """Return a store with nowhere in it yet; add_place puts a postcode there, as provider-locations would."""
    return FakeLocations()


def near(postcode: str) -> dict[str, str]:
    found = place_of(postcode)
    return {"lat": str(found.latitude), "long": str(found.longitude)}


def add_place(api, located: FakeLocations, postcode: str) -> None:
    """Store where `postcode` is and answer the EA's flood areas near it, as the fixture has them."""
    located.put(place_of(postcode))
    api.add(
        responses.GET,
        FLOOD_AREAS_URL,
        json=PLACES[postcode]["flood_areas"],
        match=[matchers.query_param_matcher(near(postcode) | {"dist": "5", "_limit": "2000"})],
    )


def add_in_force(api, **kwargs) -> None:
    api.add(responses.GET, FLOODS_URL, **(kwargs or {"json": FIXTURE["in_force"]}))


def calls_to(api, url: str) -> int:
    return sum(urlparse(call.request.url)._replace(query="").geturl() == url for call in api.calls)


def fetch(provider: FloodsProvider, ids: list[str]) -> dict:
    return asyncio.run(provider.fetch(ids))


def make_provider(located: FakeLocations, clock) -> FloodsProvider:
    return FloodsProvider(requests.Session(), located.reader, now=clock)


def test_each_postcode_gets_the_eas_warnings_for_the_flood_areas_near_it(api, located, clock):
    add_in_force(api)
    for postcode in POSTCODES:
        add_place(api, located, postcode)

    results = fetch(make_provider(located, clock), POSTCODES)

    assert list(results) == POSTCODES  # keyed by postcode, not the Redis key: the runner adds that
    for postcode in POSTCODES:
        assert json.loads(results[postcode].model_dump_json()) == PLACES[postcode]["stored"]
    # Where each is came from the store: the EA is all that was asked.
    assert {urlparse(call.request.url).netloc for call in api.calls} == {"environment.data.gov.uk"}


def test_one_list_of_the_warnings_in_force_serves_every_postcode(api, located, clock):
    add_in_force(api)
    for postcode in POSTCODES:
        add_place(api, located, postcode)
    provider = make_provider(located, clock)

    fetch(provider, POSTCODES[:2])  # one batch...
    fetch(provider, POSTCODES[2:])  # ...and the next, in the same cycle

    assert calls_to(api, FLOODS_URL) == 1
    # Asked for the whole of England, not filtered: the EA caches that request, and asks callers to keep to such.
    assert next(call.request.url for call in api.calls if call.request.url.startswith(FLOODS_URL)) == FLOODS_URL


def test_the_next_cycle_asks_for_the_warnings_in_force_again(api, located, clock):
    add_in_force(api)
    add_place(api, located, "SE17 3LL")
    provider = make_provider(located, clock)

    fetch(provider, ["SE17 3LL"])
    clock.now += timedelta(seconds=provider.interval_s)
    second = fetch(provider, ["SE17 3LL"])

    assert calls_to(api, FLOODS_URL) == 2
    assert second["SE17 3LL"].fetched_at == clock.now


def test_a_postcodes_flood_areas_are_asked_for_once_a_day(api, located, clock):
    add_in_force(api)
    add_place(api, located, "SE17 3LL")
    provider = make_provider(located, clock)

    for _ in range(3):
        fetch(provider, ["SE17 3LL"])
        clock.now += timedelta(hours=6)
    assert calls_to(api, FLOOD_AREAS_URL) == 1

    clock.now += timedelta(hours=6)  # a day after the first
    fetch(provider, ["SE17 3LL"])
    assert calls_to(api, FLOOD_AREAS_URL) == 2  # the EA redraws its flood areas now and then


def test_a_postcode_moved_by_provider_locations_gets_the_flood_areas_near_where_it_is_now(api, located, clock):
    """provider-locations looks each postcode up again once a day, and postcodes.io corrects a place now and then."""
    add_in_force(api)
    add_place(api, located, "SE17 3LL")
    add_place(api, located, "YO1 7HH")
    provider = make_provider(located, clock)
    fetch(provider, ["SE17 3LL"])

    located.put(place_of("YO1 7HH").model_copy(update={"postcode": "SE17 3LL"}))
    clock.now += timedelta(seconds=provider.interval_s)
    moved = fetch(provider, ["SE17 3LL"])["SE17 3LL"]

    assert [w.flood_area_id for w in moved.floods.items] == ["122FWF706", "122WAF940"]  # York's, not Southwark's
    assert calls_to(api, FLOOD_AREAS_URL) == 2


def test_norwegian_postcodes_are_skipped(api, located, clock):
    """They're in v1:sub:postcode for Norway's notices; nothing is asked about them."""
    results = fetch(make_provider(located, clock), ["0150", "9008"])

    assert results == {}
    assert len(api.calls) == 0


def test_norwegian_postcodes_in_a_batch_with_uk_ones_are_left_out(api, located, clock):
    add_in_force(api)
    add_place(api, located, "YO1 7HH")

    results = fetch(make_provider(located, clock), ["0150", "YO1 7HH"])

    assert list(results) == ["YO1 7HH"]


def test_a_postcode_that_doesnt_exist_is_left_out_and_nothing_asked_about_it(api, located, clock, caplog):
    """provider-locations stores it as not found, and warns about it itself, once a day."""
    add_in_force(api)
    located.put(not_found=(UNKNOWN,))
    provider = make_provider(located, clock)

    with caplog.at_level(logging.INFO):
        assert fetch(provider, [UNKNOWN]) == {}

    assert len(api.calls) == 0
    assert caplog.messages == []


def test_a_postcode_not_placed_yet_doesnt_lose_the_rest_of_the_batch(api, located, clock, caplog):
    add_in_force(api)
    add_place(api, located, "YO1 7HH")
    provider = make_provider(located, clock)

    with caplog.at_level(logging.INFO):
        results = fetch(provider, ["SE17 3LL", "YO1 7HH"])

    assert list(results) == ["YO1 7HH"]
    assert caplog.messages == [
        (
            "No location in the store yet for 1 of 2 postcodes, so no flood warnings for them this cycle "
            "(provider-locations writes them)"
        )
    ]
    assert_no_postcode_in(caplog.text, POSTCODES)


def test_the_ea_isnt_asked_while_no_postcode_in_the_batch_is_placed(api, located, clock):
    add_in_force(api)

    assert fetch(make_provider(located, clock), ["SE17 3LL"]) == {}
    assert len(api.calls) == 0


def test_one_postcode_whose_flood_areas_cant_be_had_doesnt_lose_the_rest_of_the_batch(api, located, clock, caplog):
    add_in_force(api)
    located.put(place_of("SE17 3LL"))
    api.add(
        responses.GET,
        FLOOD_AREAS_URL,
        status=500,
        match=[matchers.query_param_matcher(near("SE17 3LL"), strict_match=False)],
    )
    add_place(api, located, "YO1 7HH")

    results = fetch(make_provider(located, clock), ["SE17 3LL", "YO1 7HH"])

    assert list(results) == ["YO1 7HH"]
    # Not the postcode, nor the HTTP error's text (which quotes the URL, the postcode's coordinates and all).
    assert caplog.messages == ["Couldn't fetch the flood areas near a subscribed postcode: HTTPError 500"]
    assert_no_postcode_in(caplog.text, POSTCODES)


def test_flood_areas_the_ea_cant_give_leave_the_postcode_out_until_they_can(api, located, clock):
    add_in_force(api)
    located.put(place_of("SE17 3LL"))
    api.add(responses.GET, FLOOD_AREAS_URL, status=500)
    provider = make_provider(located, clock)

    assert fetch(provider, ["SE17 3LL"]) == {}

    api.replace(responses.GET, FLOOD_AREAS_URL, json=PLACES["SE17 3LL"]["flood_areas"])
    clock.now += timedelta(seconds=provider.interval_s)
    assert list(fetch(provider, ["SE17 3LL"])) == ["SE17 3LL"]


def test_the_ea_being_down_writes_nothing_without_naming_a_postcode(api, located, clock, caplog):
    add_in_force(api, status=503)
    for postcode in POSTCODES:
        located.put(place_of(postcode))
    provider = make_provider(located, clock)

    assert fetch(provider, ["SE17 3LL", "YO1 7HH"]) == {}
    assert fetch(provider, ["CF10 1EP"]) == {}  # the cycle's next batch doesn't ask again

    assert calls_to(api, FLOODS_URL) == 1
    assert calls_to(api, FLOOD_AREAS_URL) == 0
    assert any(message.startswith("Couldn't fetch the flood warnings in force") for message in caplog.messages)
    assert_no_postcode_in(caplog.text, POSTCODES)


def test_nothing_logged_names_a_postcode_or_its_district_or_place(api, located, clock, caplog):
    """Every way a cycle can go, logged at DEBUG by the provider and the runner: a postcode says where someone lives."""
    caplog.set_level(logging.DEBUG)
    add_in_force(api)
    # SE17 3LL isn't placed yet.
    located.put(place_of("YO1 7HH"))  # whose flood areas can't be had
    api.add(
        responses.GET,
        FLOOD_AREAS_URL,
        status=500,
        match=[matchers.query_param_matcher(near("YO1 7HH"), strict_match=False)],
    )
    add_place(api, located, "CF10 1EP")  # written
    located.put(not_found=(UNKNOWN,))
    redis = located.redis
    provider = make_provider(located, clock)

    async def cycles():
        await redis.sadd(keys.subscription(keys.POSTCODE), *POSTCODES, UNKNOWN, "0150")
        runner = ProviderRunner(provider, redis, batch_size=2)
        first = await runner.run_cycle()
        api.replace(responses.GET, FLOODS_URL, status=503)  # then the EA goes down
        clock.now += timedelta(seconds=provider.interval_s)
        return first, await runner.run_cycle()

    assert asyncio.run(cycles()) == (1, 0)
    assert len(caplog.messages) >= 4
    assert_no_postcode_in(caplog.text, [*POSTCODES, UNKNOWN])
    assert "0150" not in caplog.text


@pytest.fixture
def provider_logging(monkeypatch):
    """Configure the logs as the provider's main() does (bar the JSON handler), then put the loggers' levels back."""
    monkeypatch.setattr(log_format.logging, "basicConfig", lambda **kwargs: None)
    loggers = [logging.getLogger(name) for name in (*log_format.NOISY, *log_format.SILENCED)]
    levels = [logger.level for logger in loggers]
    floods_main.configure_logging()
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


def test_urllib3s_retry_warnings_which_quote_the_coordinates_are_kept_out(
    provider_logging, located, clock, caplog, monkeypatch
):
    """A real connection failure, retried by countdown_core's retrying session: urllib3 warns with each URL."""
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(floods_main, "FLOOD_AREAS_URL", f"http://127.0.0.1:{port}/id/floodAreas")
    session = retrying_session(backoff_factor=0)
    session.trust_env = False  # straight to the closed port, not through a proxy
    located.put(place_of("SE17 3LL"))
    provider = FloodsProvider(session, located.reader, now=clock)
    provider.in_force = (clock.now, FloodWarningsResponse())  # only the flood areas are asked for
    caplog.set_level(logging.DEBUG)

    assert asyncio.run(provider.fetch(["SE17 3LL"])) == {}

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == [
        "Couldn't fetch the flood areas near a subscribed postcode: ConnectionError"
    ]
    assert not [r for r in caplog.records if r.name.startswith("urllib3")]
    assert_no_postcode_in(caplog.text, ["SE17 3LL"])


def test_the_runner_writes_each_place_under_its_key_with_the_ttl(api, located, clock):
    add_in_force(api)
    for postcode in POSTCODES:
        add_place(api, located, postcode)
    redis = located.redis
    provider = make_provider(located, clock)

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


def test_the_flood_areas_are_asked_for_around_the_postcodes_place(api, located, clock):
    add_in_force(api)
    add_place(api, located, "YO1 7HH")

    fetch(make_provider(located, clock), ["YO1 7HH"])

    [call] = [call for call in api.calls if call.request.url.startswith(FLOOD_AREAS_URL)]
    assert parse_qs(urlparse(call.request.url).query) == {
        "lat": ["53.96197"],
        "long": ["-1.08273"],
        "dist": ["5"],
        "_limit": ["2000"],
    }
