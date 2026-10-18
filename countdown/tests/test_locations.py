"""Where the notice boards' postcodes are, through the store (`v1:location:<place>`): provider-locations and the board.

provider-locations runs against mocked postcodes.io and Kartverket (responses), and the notice board reads what it
wrote (fakeredis). fixtures/locations.json is one cycle of the provider: their answers for each postcode, and the value
it stores. fixtures/locations_board.json is what the board built, and showed, from the same answers when it looked the
postcode up itself, recorded from that code before it was removed: from the stored location it must build and show
exactly the same, for a UK postcode (in England, and in Scotland), a Norwegian one, and one of each that doesn't exist.
"""

import asyncio
import json
import logging
import socket
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fakeredis
import pydantic
import pytest
import requests
import responses
from responses import matchers
from test_utils import REGISTRATION

from countdown_core.core import log_format
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.notices import location as location_module
from countdown_core.notices import notice_board_client as board_module
from countdown_core.notices.location import POSTCODES_URL
from countdown_core.notices.norway import ADDRESSES_URL
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.store import keys, reader
from countdown_core.store.bank_holidays import StoredBankHolidays
from countdown_core.store.floods import StoredFloods
from countdown_core.store.location import StoredLocation
from countdown_core.store.met_office import StoredMetOfficeWarnings
from countdown_core.store.metalerts import StoredMetAlerts
from countdown_core.store.tfl_notices import StoredRoadDisruptions
from countdown_core.tfl.models import Postcode
from countdown_providers.locations import main as locations_main
from countdown_providers.locations.main import LOOKUP_EVERY, LocationsProvider
from countdown_providers.runner import ProviderRunner

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "locations.json").read_text(encoding="utf-8"))
PLACES = FIXTURE["postcodes"]
POSTCODES = list(PLACES)
FETCHED_AT = datetime.fromisoformat(FIXTURE["fetched_at"])
# What the board built and showed when it looked the postcode up itself.
BOARD = json.loads((FIXTURES / "locations_board.json").read_text(encoding="utf-8"))["cases"]


class Clock:
    """The provider's now: the fixture's fetched_at, until a test moves it on."""

    def __init__(self) -> None:
        self.now = FETCHED_AT

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


def answer(api, postcode: str, **kwargs) -> None:
    """Answer the lookup of `postcode` as the fixture has it (or with `kwargs`): Kartverket's, or postcodes.io's."""
    place = PLACES.get(postcode, {})
    if postcode.isdigit():
        match = [matchers.query_param_matcher({"postnummer": postcode}, strict_match=False)]
        api.add(responses.GET, ADDRESSES_URL, match=match, **(kwargs or {"json": place["geonorge"]}))
    else:
        url = f"{POSTCODES_URL}/{postcode.replace(' ', '%20')}"
        status = place.get("postcodes_io_status", 200)
        api.add(responses.GET, url, **(kwargs or {"json": place["postcodes_io"], "status": status}))


def lookups(api) -> int:
    return sum(call.request.url.startswith((POSTCODES_URL, ADDRESSES_URL)) for call in api.calls)


def make_provider(clock) -> LocationsProvider:
    # A plain session: no retries, so a mocked failure comes straight back.
    return LocationsProvider(requests.Session(), now=clock)


def fetch(provider: LocationsProvider, ids: list[str]) -> dict[str, StoredLocation]:
    return asyncio.run(provider.fetch(ids))


def traces(postcode: str) -> list[str]:
    """Return what the logs mustn't hold about `postcode`: it, its place, its district, and where it is."""
    compact = postcode.replace(" ", "").upper()
    found = (PLACES.get(postcode, {}).get("stored") or {}).get("location") or {}
    about = [str(found[k]) for k in ("latitude", "longitude", "admin_district", "region") if found.get(k)]
    return [postcode.upper(), compact, *([compact[:-3]] if not compact.isdigit() else []), *(t.upper() for t in about)]


def assert_no_postcode_in(text: str, postcodes: list[str]) -> None:
    for postcode in postcodes:
        for trace in traces(postcode):
            assert trace not in text.upper(), f"the logs mention {trace!r} ({postcode})"


# --- The provider ---------------------------------------------------------------------------------------------------


def test_each_postcode_is_stored_as_where_its_lookup_placed_it_or_as_not_found(api, clock):
    for postcode in POSTCODES:
        answer(api, postcode)

    results = fetch(make_provider(clock), POSTCODES)

    assert list(results) == POSTCODES  # keyed by the postcode, not the Redis key: the runner adds that
    for postcode in POSTCODES:
        assert json.loads(results[postcode].model_dump_json()) == PLACES[postcode]["stored"], postcode


def test_the_fixtures_stored_values_are_stored_locations():
    stored = {postcode: StoredLocation.model_validate(PLACES[postcode]["stored"]) for postcode in POSTCODES}

    assert {postcode: value.found for postcode, value in stored.items()} == {
        "SE17 3LL": True,
        "EH1 1YZ": True,
        "ZZ1 1ZZ": False,
        "0150": True,
        "0001": False,
    }
    assert stored["0150"].location == Postcode(
        postcode="0150", latitude=59.9116, longitude=10.7505, country="Norway", admin_district="Oslo"
    )


OSLO = {"postcode": "0150", "latitude": 59.9, "longitude": 10.7, "country": "Norway"}


@pytest.mark.parametrize(("found", "location"), [(True, None), (False, OSLO)])
def test_a_found_postcode_has_a_location_and_one_not_found_has_none(found, location):
    with pytest.raises(pydantic.ValidationError):
        StoredLocation(fetched_at=FETCHED_AT, postcode="0150", found=found, location=location)


def test_kartverket_is_asked_for_one_address_with_the_postcode_in_plain_latitude_and_longitude(api, clock):
    answer(api, "0150")

    fetch(make_provider(clock), ["0150"])

    [call] = api.calls
    assert parse_qs(urlparse(call.request.url).query) == {
        "postnummer": ["0150"],
        "treffPerSide": ["1"],
        "utkoordsys": ["4258"],
    }


def test_a_postcode_is_looked_up_once_written_every_cycle_and_asked_about_again_a_day_later(api, clock):
    answer(api, "SE17 3LL")
    provider = make_provider(clock)

    for _ in range(3):
        [stored] = fetch(provider, ["SE17 3LL"]).values()
        assert stored.fetched_at == FETCHED_AT  # when it was looked up, not when it was written again
        clock.now += timedelta(seconds=provider.interval_s)
    assert lookups(api) == 1

    clock.now = FETCHED_AT + LOOKUP_EVERY
    [stored] = fetch(provider, ["SE17 3LL"]).values()
    assert lookups(api) == 2
    assert stored.fetched_at == clock.now


def test_a_postcode_that_cant_be_looked_up_is_left_out_and_tried_again_next_cycle(api, clock, caplog):
    answer(api, "SE17 3LL", status=503)
    answer(api, "SE17 3LL")
    provider = make_provider(clock)

    with caplog.at_level("WARNING", logger="countdown_providers.locations.main"):
        assert fetch(provider, ["SE17 3LL"]) == {}  # the board waits rather than say it doesn't exist
    clock.now += timedelta(seconds=provider.interval_s)
    assert fetch(provider, ["SE17 3LL"])["SE17 3LL"].found

    # Not the HTTPError's own text: that quotes the URL, postcode and all.
    assert caplog.messages == ["Couldn't look up a postcode: HTTPError 503"]


def test_kartverket_being_unreachable_leaves_a_norwegian_postcode_out_until_it_answers(api, clock, caplog):
    answer(api, "0150", body=requests.ConnectionError(f"{ADDRESSES_URL}?postnummer=0150"))
    provider = make_provider(clock)

    with caplog.at_level("WARNING", logger="countdown_providers.locations.main"):
        assert fetch(provider, ["0150"]) == {}

    assert caplog.messages == ["Couldn't look up a postcode: ConnectionError"]


def test_a_lookup_that_fails_keeps_the_last_answer_until_one_succeeds(api, clock):
    answer(api, "SE17 3LL")
    answer(api, "SE17 3LL", status=500)
    provider = make_provider(clock)
    first = fetch(provider, ["SE17 3LL"])["SE17 3LL"]

    clock.now += LOOKUP_EVERY
    assert fetch(provider, ["SE17 3LL"])["SE17 3LL"] == first
    clock.now += timedelta(seconds=provider.interval_s)
    assert fetch(provider, ["SE17 3LL"])["SE17 3LL"] == first
    assert lookups(api) == 3  # asked every cycle until it answers


def test_a_postcode_found_before_isnt_unplaced_by_a_later_not_found(api, clock, caplog):
    """postcodes.io drops a terminated postcode; the screens that have it keep their local notices."""
    answer(api, "SE17 3LL")
    answer(api, "SE17 3LL", status=404, json={"status": 404, "error": "Postcode not found"})
    provider = make_provider(clock)
    first = fetch(provider, ["SE17 3LL"])["SE17 3LL"]

    clock.now += LOOKUP_EVERY
    with caplog.at_level("WARNING", logger="countdown_providers.locations.main"):
        kept = fetch(provider, ["SE17 3LL"])["SE17 3LL"]
    clock.now += timedelta(hours=1)
    fetch(provider, ["SE17 3LL"])

    assert (kept.found, kept.location) == (True, first.location)
    assert kept.fetched_at == FETCHED_AT + LOOKUP_EVERY  # and asked about again a day after that, not every cycle
    assert lookups(api) == 2
    assert caplog.messages == ["A postcode that was found before isn't found now; keeping where it was"]


def test_a_postcode_not_found_is_asked_about_again_a_day_later_and_placed_once_it_exists(api, clock, caplog):
    answer(api, "SE17 3LL", status=404, json={"status": 404, "error": "Postcode not found"})
    answer(api, "SE17 3LL")
    provider = make_provider(clock)

    with caplog.at_level("WARNING", logger="countdown_providers.locations.main"):
        assert not fetch(provider, ["SE17 3LL"])["SE17 3LL"].found
        clock.now += timedelta(hours=23)
        assert not fetch(provider, ["SE17 3LL"])["SE17 3LL"].found
    assert lookups(api) == 1
    clock.now = FETCHED_AT + LOOKUP_EVERY
    assert fetch(provider, ["SE17 3LL"])["SE17 3LL"].found

    assert caplog.messages == ["A subscribed postcode isn't one postcodes.io or Kartverket knows; stored as not found"]


def test_something_neither_a_uk_nor_a_norwegian_postcode_is_stored_as_not_found_without_asking(api, clock):
    results = fetch(make_provider(clock), ["SE17", "12345", "Oslo"])

    assert [stored.found for stored in results.values()] == [False, False, False]
    assert len(api.calls) == 0


def test_the_runner_writes_each_postcodes_location_under_its_place_for_days(api, clock):
    for postcode in POSTCODES:
        answer(api, postcode)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    provider = make_provider(clock)

    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), *POSTCODES)
        written = await ProviderRunner(provider, redis).run_cycle()
        values = {postcode: await redis.get(f"v1:location:{postcode.replace(' ', '')}") for postcode in POSTCODES}
        return written, values, [await redis.ttl(keys.location(postcode)) for postcode in POSTCODES]

    written, values, ttls = asyncio.run(cycle())

    assert written == len(POSTCODES)  # a postcode that doesn't exist is written too, so its board can say so
    assert {postcode: json.loads(value) for postcode, value in values.items()} == {
        postcode: PLACES[postcode]["stored"] for postcode in POSTCODES
    }
    assert all(0 < ttl <= provider.ttl_s for ttl in ttls)
    assert provider.ttl_s >= 2 * LOOKUP_EVERY.total_seconds() > provider.interval_s


def test_nothing_logged_names_a_postcode_or_anything_about_its_place(api, clock, caplog):
    """Every way a cycle can go, logged at DEBUG by the provider and the runner: a postcode says where someone lives."""
    caplog.set_level(logging.DEBUG)
    answer(api, "SE17 3LL", status=500)  # can't be looked up
    answer(api, "EH1 1YZ")  # found, then not
    answer(api, "EH1 1YZ", status=404, json={"status": 404, "error": "Postcode not found"})
    answer(api, "ZZ1 1ZZ")  # doesn't exist
    answer(api, "0150")  # found, then Kartverket is unreachable
    answer(api, "0150", body=requests.ConnectionError(f"{ADDRESSES_URL}?postnummer=0150"))
    answer(api, "0001")  # doesn't exist
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    provider = make_provider(clock)

    async def cycles():
        await redis.sadd(keys.subscription(keys.POSTCODE), *POSTCODES)
        runner = ProviderRunner(provider, redis, batch_size=2)
        first = await runner.run_cycle()
        clock.now += LOOKUP_EVERY
        return first, await runner.run_cycle()

    assert asyncio.run(cycles()) == (4, 4)
    assert len(caplog.messages) >= 6
    assert_no_postcode_in(caplog.text, POSTCODES)


@pytest.fixture
def provider_logging(monkeypatch):
    """Configure the logs as the provider's main() does (bar the JSON handler), then put the loggers' levels back."""
    monkeypatch.setattr(log_format.logging, "basicConfig", lambda **kwargs: None)
    loggers = [logging.getLogger(name) for name in (*log_format.NOISY, *log_format.SILENCED)]
    levels = [logger.level for logger in loggers]
    locations_main.configure_logging()
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


def test_urllib3s_retry_warnings_which_quote_the_postcodes_url_are_kept_out(
    provider_logging, clock, caplog, monkeypatch
):
    """A real connection failure, retried by the renderer's own retrying session: urllib3 warns with each URL."""
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(location_module, "POSTCODES_URL", f"http://127.0.0.1:{port}/postcodes")
    session = AbstractClient._build_retrying_session(backoff_factor=0)
    session.trust_env = False  # straight to the closed port, not through a proxy
    caplog.set_level(logging.DEBUG)

    assert asyncio.run(LocationsProvider(session, now=clock).fetch(["SE17 3LL"])) == {}

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == [
        "Couldn't look up a postcode: ConnectionError"
    ]
    assert not [r for r in caplog.records if r.name.startswith("urllib3")]
    assert_no_postcode_in(caplog.text, ["SE17 3LL"])


# --- The notice board reading it: what it built when it looked the postcode up itself ----------------------------


def fill_notices(store, postcode: str, now: datetime) -> None:
    """Store the notices the providers would have, as when locations_board.json was recorded."""
    gov_uk = json.loads((FIXTURES / "bank_holidays.json").read_text(encoding="utf-8"))
    store.set(keys.bank_holidays(), StoredBankHolidays(fetched_at=now, divisions=gov_uk).model_dump_json())
    metalerts = json.loads((FIXTURES / "metalerts_current.json").read_text(encoding="utf-8"))
    store.set(keys.metalerts(), StoredMetAlerts.model_validate(metalerts).model_dump_json())
    warnings = json.loads((FIXTURES / "met_office_warnings.json").read_text(encoding="utf-8"))
    store.set(
        keys.met_office(postcode),
        StoredMetOfficeWarnings.model_validate(warnings | {"postcode": postcode}).model_dump_json(),
    )
    floods = StoredFloods(fetched_at=now, postcode=postcode, floods={"items": []})
    store.set(keys.floods(postcode), floods.model_dump_json())
    roads = StoredRoadDisruptions(fetched_at=now, postcode=postcode, disruptions=[])
    store.set(keys.tfl_roads(postcode), roads.model_dump_json())


def describe(source) -> dict:
    """Say what a source is and what it's for, as locations_board.json has it."""
    described = {"type": type(source).__name__}
    for attr in ("division", "region", "key", "postcode"):
        if hasattr(source, attr):
            described[attr] = getattr(source, attr)
    if hasattr(source, "location"):
        described["location"] = source.location.model_dump()
    return described


def notice_json(notice) -> dict:
    return {
        "source": notice.source,
        "severity": notice.severity.name,
        "text": notice.text,
        "valid_until": notice.valid_until and notice.valid_until.isoformat(),
        "display_time": notice.display_time and notice.display_time.isoformat(),
    }


@pytest.fixture
def provider_redis(store, monkeypatch):
    """Return the providers' (async) side of a store, and its (sync) other side, which the renderer reads."""
    server = fakeredis.FakeServer()
    renderer = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: renderer)
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=True), renderer


def freeze_board_clock(monkeypatch, now: datetime) -> None:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(board_module, "datetime", Frozen)


@pytest.mark.parametrize("case", BOARD, ids=[case["postcode"] for case in BOARD])
def test_the_board_builds_and_shows_from_the_stored_location_what_it_did_from_its_own_lookup(
    case, provider_redis, clock, monkeypatch
):
    redis, renderer_store = provider_redis
    postcode, now = case["postcode"], datetime.fromisoformat(case["now"])

    async def provide():
        await redis.sadd(keys.subscription(keys.POSTCODE), postcode)
        return await ProviderRunner(make_provider(clock), redis).run_cycle()

    with responses.RequestsMock(assert_all_requests_are_fired=False) as upstream:
        answer(upstream, postcode)
        assert asyncio.run(provide()) == 1
    fill_notices(renderer_store, postcode, now)
    freeze_board_clock(monkeypatch, now)
    board = NoticeBoardClient(REGISTRATION, NoticeBoardSettings(postcode=postcode, country=case["country"]))

    with responses.RequestsMock() as nothing:  # the board asks nobody: any request would fail
        panel = asyncio.run(board.update())
        assert len(nothing.calls) == 0

    assert (board.location and board.location.model_dump()) == case["location"]
    assert [describe(source) for source in board.sources] == case["sources"]
    assert getattr(panel, "message", None) == case["message"]
    assert ([notice_json(n) for n in panel.notices] if hasattr(panel, "notices") else None) == case["notices"]


def test_the_recorded_board_covers_a_uk_a_norwegian_and_an_unknown_postcode():
    assert {(case["country"], case["location"] is not None) for case in BOARD} == {
        ("GB", True),
        ("GB", False),
        ("NO", True),
        ("NO", False),
    }
    assert {case["postcode"] for case in BOARD} == set(POSTCODES)
