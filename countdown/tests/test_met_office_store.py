"""The Met Office's weather warnings through the store (`v1:notices:met-office:<place>`).

provider-met-office runs against mocked postcodes.io and Met Office feeds (responses), and the notice board's source
reads what it wrote back (fakeredis).

fixtures/met_office_warnings.xml is a region's feed, and fixtures/met_office_warnings.json the stored value the
provider makes of it. The notices the board makes from the stored value must be the ones it made from the feed when it
fetched the feed itself: fixtures/met_office_notices.json is what it made then, from that feed at the fixture's `now`,
in English and in Norwegian. One change since: an amber warning now reads "Oransje farevarsel" in Norwegian, not
"Gult", since amber is the Met Office's middle level, as orange is MetAlerts'.
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import fakeredis
import pytest
import requests
import responses
from test_utils import REGISTRATION

from countdown_core import i18n
from countdown_core.notices import notice_board_client as board_module
from countdown_core.notices.location import POSTCODES_URL
from countdown_core.notices.met_office import NoStoredWarningsError, StoredMetOfficeWarningsSource, warning_notices
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.store import keys, reader
from countdown_core.store.met_office import StoredMetOfficeWarnings
from countdown_core.tfl.models import Postcode
from countdown_providers.met_office.main import FEED_URL, MetOfficeProvider
from countdown_providers.runner import ProviderRunner

FIXTURES = Path(__file__).parent / "fixtures"
FEED = (FIXTURES / "met_office_warnings.xml").read_bytes()
STORED = json.loads((FIXTURES / "met_office_warnings.json").read_text())
# The notices the board made from FEED when it fetched the Met Office's feed itself.
FEED_NOTICES = json.loads((FIXTURES / "met_office_notices.json").read_text())
NOW = datetime.fromisoformat(FEED_NOTICES["now"])
FETCHED_AT = datetime.fromisoformat(STORED["fetched_at"])

SOUTHWARK = Postcode(
    postcode="SE17 3LL", latitude=51.49, longitude=-0.1, country="England", region="London", admin_district="Southwark"
)
BRIGHTON = Postcode(
    postcode="BN1 1AA",
    latitude=50.82,
    longitude=-0.14,
    country="England",
    region="South East",
    admin_district="Brighton and Hove",
)
EXETER = Postcode(
    postcode="EX1 1AA", latitude=50.72, longitude=-3.53, country="England", region="South West", admin_district="Exeter"
)
NOWHERE_KNOWN = Postcode(
    postcode="KW1 4YT", latitude=58.4, longitude=-3.1, country="Scotland", region=None, admin_district="Somewhere New"
)
OSLO = "0150"

EMPTY = b"<rss><channel><title>Met Office warnings</title></channel></rss>"


@pytest.fixture
def api():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture(params=["en", "nb"])
def language(request):
    """Draw in English, then in Norwegian (the screen's language decides the notices' words, as they're drawn)."""
    i18n.set_locale(request.param, "Europe/London" if request.param == "en" else "Europe/Oslo")
    yield request.param
    i18n.set_locale("en", "Europe/London")


class Clock:
    def __init__(self, now: datetime = FETCHED_AT) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_provider(clock: Clock | None = None) -> MetOfficeProvider:
    # A plain session: no retries, so a mocked failure comes straight back.
    return MetOfficeProvider(requests.Session(), now=clock or Clock())


def add_postcode(api, location: Postcode) -> None:
    api.add(
        responses.GET,
        f"{POSTCODES_URL}/{location.postcode.replace(' ', '%20')}",
        json={"result": location.model_dump()},
    )


def add_feed(api, region: str, feed: bytes = FEED, **kwargs) -> None:
    api.add(responses.GET, FEED_URL.format(region=region), body=feed, **kwargs)


def calls_to(api, url: str) -> int:
    return sum(call.request.url == url for call in api.calls)


def feed_calls(api, region: str) -> int:
    return calls_to(api, FEED_URL.format(region=region))


def as_json(notice: Notice) -> dict:
    return {
        "source": notice.source,
        "severity": notice.severity.name,
        "text": notice.text,
        "valid_until": notice.valid_until.isoformat() if notice.valid_until else None,
        "display_time": notice.display_time.isoformat() if notice.display_time else None,
    }


def assert_same_notices(actual: list[Notice], expected: list[dict]) -> None:
    assert [as_json(notice) for notice in actual] == expected


# --- The provider -------------------------------------------------------------------


def test_the_stored_value_is_the_regions_feed_items_as_the_feed_has_them(api):
    add_postcode(api, SOUTHWARK)
    add_feed(api, "se")

    results = asyncio.run(make_provider().fetch(["SE17 3LL"]))

    assert list(results) == ["SE17 3LL"]  # keyed by the postcode, not the Redis key: the runner adds that
    assert json.loads(results["SE17 3LL"].model_dump_json()) == STORED


def test_the_fixtures_stored_value_is_a_stored_warnings():
    stored = StoredMetOfficeWarnings.model_validate(STORED)

    assert stored.region == "se"
    assert [item.title for item in stored.items][:2] == [
        "Amber warning of wind affecting London & South East England",
        "Yellow warning of rain affecting London & South East England",
    ]


def test_each_regions_feed_is_fetched_once_a_cycle_however_many_postcodes_share_it(api):
    for location in (SOUTHWARK, BRIGHTON, EXETER):
        add_postcode(api, location)
    add_feed(api, "se")
    add_feed(api, "sw", EMPTY)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    provider = make_provider()

    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), "SE17 3LL", "BN1 1AA", "EX1 1AA")
        # A batch a postcode, so the two in London & South East England are asked for apart.
        return await ProviderRunner(provider, redis, batch_size=1).run_cycle()

    assert asyncio.run(cycle()) == 3
    assert (feed_calls(api, "se"), feed_calls(api, "sw")) == (1, 1)
    stored = {
        postcode: StoredMetOfficeWarnings.model_validate_json(asyncio.run(redis.get(keys.met_office(postcode))))
        for postcode in ("SE17 3LL", "BN1 1AA", "EX1 1AA")
    }
    assert [(s.postcode, s.region, len(s.items)) for s in stored.values()] == [
        ("SE17 3LL", "se", 6),
        ("BN1 1AA", "se", 6),
        ("EX1 1AA", "sw", 0),
    ]


def test_the_next_cycle_fetches_the_feeds_again_but_places_each_postcode_only_once(api):
    add_postcode(api, SOUTHWARK)
    add_postcode(api, BRIGHTON)
    add_feed(api, "se")
    clock = Clock()
    provider = make_provider(clock)

    asyncio.run(provider.fetch(["SE17 3LL"]))
    asyncio.run(provider.fetch(["BN1 1AA"]))  # the same cycle's next batch
    clock.now += timedelta(seconds=provider.interval_s)
    results = asyncio.run(provider.fetch(["SE17 3LL", "BN1 1AA"]))

    assert feed_calls(api, "se") == 2
    assert calls_to(api, f"{POSTCODES_URL}/SE17%203LL") == 1
    assert {s.fetched_at for s in results.values()} == {clock.now}


def test_norwegian_postcodes_are_left_to_met_norway(api):
    assert asyncio.run(make_provider().fetch([OSLO])) == {}
    assert len(api.calls) == 0


def test_a_postcode_with_no_region_it_knows_gets_the_whole_uks_feed(api):
    add_postcode(api, NOWHERE_KNOWN)
    add_feed(api, "UK", EMPTY)

    results = asyncio.run(make_provider().fetch(["KW1 4YT"]))

    assert results["KW1 4YT"].region == "UK"
    assert results["KW1 4YT"].items == []


def test_an_unknown_postcode_is_left_out_and_not_asked_about_again(api, caplog):
    api.add(responses.GET, f"{POSTCODES_URL}/ZZ1%201ZZ", status=404, json={"error": "Invalid postcode"})
    provider = make_provider()

    with caplog.at_level("WARNING", logger="countdown_providers.met_office.main"):
        assert asyncio.run(provider.fetch(["ZZ1 1ZZ"])) == {}
        assert asyncio.run(provider.fetch(["ZZ1 1ZZ"])) == {}

    assert len(api.calls) == 1
    # Where the screens are stays out of the logs.
    assert caplog.messages == ["postcodes.io doesn't know one of the postcodes, so it gets no Met Office warnings"]


def test_a_postcode_that_couldnt_be_looked_up_is_left_out_and_tried_again(api, caplog):
    api.add(responses.GET, f"{POSTCODES_URL}/SE17%203LL", status=503)
    add_postcode(api, SOUTHWARK)
    add_feed(api, "se")
    provider = make_provider()

    with caplog.at_level("WARNING", logger="countdown_providers.met_office.main"):
        assert asyncio.run(provider.fetch(["SE17 3LL"])) == {}
    assert list(asyncio.run(provider.fetch(["SE17 3LL"]))) == ["SE17 3LL"]
    # Not the HTTPError's own text: that quotes the URL, postcode and all.
    assert caplog.messages == ["Couldn't look up a postcode: HTTPError"]


def test_a_region_whose_feed_fails_leaves_its_postcodes_out_and_the_others_carry_on(api, caplog):
    add_postcode(api, SOUTHWARK)
    add_postcode(api, BRIGHTON)
    add_postcode(api, EXETER)
    add_feed(api, "se", status=500)
    add_feed(api, "sw", EMPTY)

    with caplog.at_level("WARNING", logger="countdown_providers.met_office.main"):
        results = asyncio.run(make_provider().fetch(["SE17 3LL", "BN1 1AA", "EX1 1AA"]))

    assert list(results) == ["EX1 1AA"]
    assert feed_calls(api, "se") == 1  # failed once this cycle: not asked again for the second postcode
    # Not the region, nor the feed's URL with it in: a region is worked out from a postcode.
    assert caplog.messages == ["Couldn't fetch a region's Met Office warnings: HTTPError 500"]


def test_a_feed_that_isnt_xml_is_a_failed_fetch(api):
    add_postcode(api, SOUTHWARK)
    add_feed(api, "se", b"<html>Service unavailable")

    assert asyncio.run(make_provider().fetch(["SE17 3LL"])) == {}


def test_the_runner_writes_each_postcodes_warnings_under_its_place_with_the_ttl(api):
    add_postcode(api, SOUTHWARK)
    add_feed(api, "se")
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    provider = make_provider()

    async def cycle():
        await redis.sadd(keys.subscription(keys.POSTCODE), "SE17 3LL", OSLO)
        written = await ProviderRunner(provider, redis).run_cycle()
        key = "v1:notices:met-office:SE173LL"
        return written, await redis.get(key), await redis.ttl(key), await redis.keys("v1:notices:*")

    written, value, ttl, written_keys = asyncio.run(cycle())

    assert written == 1
    assert written_keys == ["v1:notices:met-office:SE173LL"]
    assert 0 < ttl <= provider.ttl_s
    assert provider.ttl_s > provider.interval_s
    assert json.loads(value) == STORED


# --- The notice board reading it ----------------------------------------------------


def test_the_stored_warnings_make_the_notices_the_feed_made(language):
    stored = StoredMetOfficeWarnings.model_validate(STORED)

    assert_same_notices(warning_notices(stored.items, NOW), FEED_NOTICES[language])


def test_the_stored_source_reads_its_postcodes_key(store, language):
    store.set("v1:notices:met-office:SE173LL", json.dumps(STORED))
    source = StoredMetOfficeWarningsSource(requests.Session(), SOUTHWARK, "SE17 3LL")

    assert source.region == "se"
    assert_same_notices(source.fetch(NOW), FEED_NOTICES[language])


def test_a_postcode_not_in_the_store_raises_a_lookup_error_that_doesnt_say_where(store):
    source = StoredMetOfficeWarningsSource(requests.Session(), SOUTHWARK, "SE17 3LL")

    with pytest.raises(NoStoredWarningsError) as raised:
        source.fetch(NOW)

    assert isinstance(raised.value, LookupError)
    assert "SE17" not in str(raised.value)


def test_a_renderer_without_a_store_fails_only_this_source_and_only_when_it_reads(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    source = StoredMetOfficeWarningsSource(requests.Session(), SOUTHWARK, "SE17 3LL")  # doesn't raise

    with pytest.raises(reader.StoreNotConfiguredError):
        source.fetch(NOW)


@pytest.fixture
def clock(monkeypatch):
    class BoardClock:
        now = NOW

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return BoardClock.now

    monkeypatch.setattr(board_module, "datetime", FrozenDatetime)
    return BoardClock


def board_with_source(source) -> NoticeBoardClient:
    client = NoticeBoardClient(REGISTRATION, NoticeBoardSettings(postcode=None))
    client.sources = [source]
    return client


def test_the_board_keeps_the_last_warnings_while_the_key_is_missing(store, clock):
    store.set(keys.met_office("SE17 3LL"), json.dumps(STORED))
    client = board_with_source(StoredMetOfficeWarningsSource(requests.Session(), SOUTHWARK, "SE17 3LL"))
    first = asyncio.run(client.update()).notices

    store.delete(keys.met_office("SE17 3LL"))  # the provider's down, and its key has lapsed
    clock.now += timedelta(minutes=10)

    assert asyncio.run(client.update()).notices == first
    # The thunderstorm warning had ended by NOW, so the board leaves it out.
    assert [n.severity for n in first] == [Severity.WARNING, Severity.WARNING, Severity.INFO, Severity.INFO]


def test_the_board_shows_what_the_provider_wrote_for_its_postcode(api, clock, language):
    """End to end: the provider writes the store, the board reads it, and shows the notices still in force."""
    add_postcode(api, SOUTHWARK)
    add_feed(api, "se")
    server = fakeredis.FakeServer()
    provider_redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    renderer_redis = fakeredis.FakeRedis(server=server, decode_responses=True)

    async def cycle():
        await provider_redis.sadd(keys.subscription(keys.POSTCODE), "SE17 3LL")
        await ProviderRunner(make_provider(Clock(NOW)), provider_redis).run_cycle()

    asyncio.run(cycle())
    board = board_with_source(StoredMetOfficeWarningsSource(requests.Session(), SOUTHWARK, "SE17 3LL", renderer_redis))

    in_force = [
        n for n in FEED_NOTICES[language] if n["valid_until"] is None or datetime.fromisoformat(n["valid_until"]) > NOW
    ]
    assert_same_notices(asyncio.run(board.update()).notices, sorted(in_force, key=lambda n: Severity[n["severity"]]))


def test_the_board_reads_the_warnings_under_its_own_postcode_once_its_placed(api, store, clock, caplog):
    add_postcode(api, SOUTHWARK)
    store.set("v1:notices:met-office:SE173LL", json.dumps(STORED))
    client = NoticeBoardClient(REGISTRATION, NoticeBoardSettings(postcode="SE17 3LL"))
    client._refresh = lambda source, now: None  # only the lookup is under test here

    with caplog.at_level("INFO", logger="countdown_core.notices.notice_board_client"), pytest.raises(RuntimeError):
        asyncio.run(client.update())  # sources added, but none has answered yet

    [source] = [s for s in client.sources if isinstance(s, StoredMetOfficeWarningsSource)]
    assert_same_notices(source.fetch(NOW), FEED_NOTICES["en"])
    assert source.region == "se"
    # Placed, but not where: a region says where the screen is.
    assert "Notice board: location lookup succeeded; bank holidays for england-and-wales" in caplog.messages
