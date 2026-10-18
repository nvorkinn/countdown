"""Entur's disruptions at the screen's stops, through the store (fakeredis), with Entur mocked (responses).

provider-entur-situations writes Entur's own answer for each stop to `v1:notices:entur:<stop id>`, and the notice
board makes the notices from it as it draws (StoredEnturSituationsSource). fixtures/entur_situations.json holds
Entur's answers for a stop place, a quay and a stop Entur doesn't know, and the notices (in English and Norwegian)
that the board made from those same answers, as of the fixture's `now`, when it still asked Entur itself (the
EnturSituationsSource it had before the provider): the stored path must make the same.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fakeredis
import pytest
import requests
import responses
from test_utils import REGISTRATION

from countdown_api.models import DepartureStop
from countdown_core import i18n
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.entur.entur_client import HEADERS, JOURNEY_PLANNER_URL, EnturClient
from countdown_core.notices import notice_board_client as board_module
from countdown_core.notices.entur import (
    QUAY_SITUATIONS_QUERY,
    SITUATIONS_QUERY,
    NoStoredSituationsError,
    StoredEnturSituationsSource,
)
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.store import keys, reader
from countdown_core.store.situations import StoredSituations
from countdown_providers.entur_situations.main import QUOTA_BACKOFF, EnturSituationsProvider
from countdown_providers.runner import ProviderRunner

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "entur_situations.json").read_text())
NOW = datetime.fromisoformat(FIXTURE["now"])
STOP_IDS: list[str] = FIXTURE["stop_ids"]
ANSWERS: dict[str, dict] = FIXTURE["answers"]
MAJORSTUEN_ID, QUAY_ID, UNKNOWN_ID = STOP_IDS
FAILING_ID = "NSR:StopPlace:666"
BUSY_ID = "NSR:StopPlace:429"


def pinned(language: str) -> list[Notice]:
    """Return the notices the board made from the fixture's answers when it asked Entur itself."""
    return [
        Notice(
            notice["source"],
            Severity[notice["severity"]],
            notice["text"],
            datetime.fromisoformat(notice["valid_until"]) if notice["valid_until"] else None,
        )
        for notice in FIXTURE["notices"][language]
    ]


@pytest.fixture
def entur():
    """Mock Entur: the fixture's answer for each of its stops, a 500 for FAILING_ID, a 429 for BUSY_ID.

    Any other stop it doesn't know, and answers as Entur does.
    """

    def answer(request):
        stop_id = json.loads(request.body)["variables"]["id"]
        if stop_id == FAILING_ID:
            return 500, {}, json.dumps({"message": "Entur is having a bad day"})
        if stop_id == BUSY_ID:
            return 429, {"Rate-Limit-Range": "per-minute"}, json.dumps({"message": "Quota exceeded"})
        unknown = {"quay" if stop_id.startswith("NSR:Quay:") else "stopPlace": None}
        return 200, {}, json.dumps({"data": ANSWERS.get(stop_id, unknown)})

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.POST, JOURNEY_PLANNER_URL, callback=answer, content_type="application/json")
        yield rsps


@pytest.fixture(params=["en", "nb"])
def language(request):
    """Draw the screen in English, then in Norwegian, on Oslo's time."""
    i18n.set_locale(request.param, "Europe/Oslo")
    yield request.param
    i18n.set_locale("en", "Europe/London")


def make_provider(now=lambda: NOW) -> EnturSituationsProvider:
    # A plain session, so a 500 isn't retried.
    return EnturSituationsProvider(EnturClient(requests.Session()), now=now)


def queried(entur) -> list[tuple[str, str]]:
    """Return each query asked of (mocked) Entur as (its id variable, which query it was)."""
    asked = []
    for call in entur.calls:
        body = json.loads(call.request.body)
        asked.append((body["variables"]["id"], "quay" if body["query"] == QUAY_SITUATIONS_QUERY else "stopPlace"))
    return asked


# --- The provider -------------------------------------------------------------------


def test_the_provider_stores_enturs_own_answer_for_each_stop_with_when_it_was_fetched(entur):
    results = asyncio.run(make_provider().fetch(STOP_IDS))

    assert results == {
        stop_id: StoredSituations(fetched_at=NOW, stop_id=stop_id, data=ANSWERS[stop_id]) for stop_id in STOP_IDS
    }


def test_the_provider_asks_as_the_board_did_named_and_with_the_quays_query_for_a_quay(entur):
    asyncio.run(make_provider().fetch([MAJORSTUEN_ID, QUAY_ID, "58366"]))

    # A bare number is a stop place, as the departures take it; the key keeps the id as subscribed.
    assert queried(entur) == [(MAJORSTUEN_ID, "stopPlace"), (QUAY_ID, "quay"), ("NSR:StopPlace:58366", "stopPlace")]
    assert json.loads(entur.calls[0].request.body)["query"] == SITUATIONS_QUERY
    assert all(call.request.headers["ET-Client-Name"] == HEADERS["ET-Client-Name"] for call in entur.calls)


def test_a_stop_entur_does_not_know_is_still_stored_as_entur_answered(entur, caplog):
    [stored] = asyncio.run(make_provider().fetch([UNKNOWN_ID])).values()

    assert stored.data == {"stopPlace": None}
    assert stored.stop() is None
    assert f"Entur doesn't know stop {UNKNOWN_ID}" in caplog.text


def test_a_failing_stop_is_left_out_without_losing_the_batch(entur, caplog):
    results = asyncio.run(make_provider().fetch([FAILING_ID, MAJORSTUEN_ID]))

    assert list(results) == [MAJORSTUEN_ID]
    assert f"Couldn't fetch the situations at Entur stop {FAILING_ID}" in caplog.text


def test_once_enturs_quota_is_used_up_the_rest_wait_until_it_is_back(entur, caplog):
    clock = {"now": NOW}
    provider = make_provider(now=lambda: clock["now"])

    assert asyncio.run(provider.fetch([BUSY_ID, MAJORSTUEN_ID, QUAY_ID])) == {}
    assert queried(entur) == [(BUSY_ID, "stopPlace")]
    assert "2 stops wait for the next cycle" in caplog.text

    clock["now"] = NOW + QUOTA_BACKOFF
    assert list(asyncio.run(provider.fetch([MAJORSTUEN_ID, QUAY_ID]))) == [MAJORSTUEN_ID, QUAY_ID]


def test_the_runner_writes_each_stops_answer_under_its_notices_key_with_the_ttl(entur):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    provider = make_provider()

    async def cycle():
        await redis.sadd(keys.subscription(keys.ENTUR), MAJORSTUEN_ID, FAILING_ID)
        written = await ProviderRunner(provider, redis).run_cycle()
        key = keys.entur_situations(MAJORSTUEN_ID)
        return (
            written,
            await redis.get(key),
            await redis.ttl(key),
            await redis.exists(keys.entur_situations(FAILING_ID)),
        )

    written, value, ttl, failing_exists = asyncio.run(cycle())

    assert (written, failing_exists) == (1, 0)
    assert provider.interval_s < ttl <= provider.ttl_s
    # The stored shape: Entur's GraphQL `data`, as it came, in a thin wrapper.
    assert json.loads(value) == {
        "fetched_at": "2026-10-16T12:00:00Z",
        "stop_id": MAJORSTUEN_ID,
        "data": ANSWERS[MAJORSTUEN_ID],
    }


def test_the_situations_key_is_in_the_notices_namespace_by_stop_id():
    assert keys.entur_situations(QUAY_ID) == "v1:notices:entur:NSR:Quay:7333"
    assert make_provider().output_key(MAJORSTUEN_ID) == "v1:notices:entur:NSR:StopPlace:58381"
    assert make_provider().name == keys.ENTUR


# --- The board, from the store ------------------------------------------------------


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def store(store, server, monkeypatch):
    """Return the renderer's store, on a server the provider's (async) client can write to as well."""
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: client)
    return client


@pytest.fixture
def provide(server, entur):
    """Return a function running one provider cycle (at NOW) over the stops it's given; it returns the keys written.

    It writes to the renderer's store, from mocked Entur; afterwards Entur's calls are forgotten, so a test can check
    that the board asked it nothing.
    """

    def cycle(*stop_ids: str) -> int:
        redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

        async def run():
            await redis.delete(keys.subscription(keys.ENTUR))
            await redis.sadd(keys.subscription(keys.ENTUR), *stop_ids)
            return await ProviderRunner(make_provider(), redis).run_cycle()

        written = asyncio.run(run())
        entur.calls.reset()
        return written

    return cycle


def test_the_stored_path_makes_the_notices_the_board_made_from_entur_itself(store, provide, entur, language):
    assert provide(*STOP_IDS) == len(STOP_IDS)

    from_store = StoredEnturSituationsSource(store, STOP_IDS).fetch(NOW)

    assert from_store == pinned(language)
    assert len(entur.calls) == 0  # the board only read the store


def test_the_language_is_the_screens_as_it_draws_not_as_the_provider_fetched(store, provide, entur):
    provide(*STOP_IDS)
    source = StoredEnturSituationsSource(store, STOP_IDS)
    try:
        i18n.set_locale("en", "Europe/Oslo")
        english = source.fetch(NOW)
        i18n.set_locale("nb", "Europe/Oslo")
        norwegian = source.fetch(NOW)
    finally:
        i18n.set_locale("en", "Europe/London")

    assert (english, norwegian) == (pinned("en"), pinned("nb"))
    assert (english[2].source, norwegian[2].source) == ("Line 5", "Linje 5")
    assert len(entur.calls) == 0


def test_a_situation_that_has_ended_since_the_provider_fetched_it_is_left_out(store, provide):
    provide(*STOP_IDS)
    later = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # "Bus for metro" ended on the 20th

    notices = StoredEnturSituationsSource(store, STOP_IDS).fetch(later)

    assert notices == [n for n in pinned("en") if n.valid_until is None or n.valid_until >= later]
    assert "Bus for metro" not in [n.text for n in notices]
    assert len(notices) == len(pinned("en")) - 1


def test_with_nothing_in_the_store_for_any_stop_it_raises_a_lookup_error(store):
    with pytest.raises(NoStoredSituationsError) as raised:
        StoredEnturSituationsSource(store, [MAJORSTUEN_ID, QUAY_ID]).fetch(NOW)

    assert isinstance(raised.value, LookupError)
    assert MAJORSTUEN_ID in str(raised.value)


def test_a_stop_the_store_has_nothing_for_yet_is_left_out_and_said(store, provide, caplog):
    provide(QUAY_ID)

    notices = StoredEnturSituationsSource(store, [MAJORSTUEN_ID, QUAY_ID]).fetch(NOW)

    assert [n.text for n in notices] == ["Bus for metro", "Buses replace the train to Frognerseteren"]
    # Said once, as the quay's own situation; the line's isn't said again.
    assert notices[0].source == "Majorstuen"
    assert f"No situations in the store for Entur stops {MAJORSTUEN_ID}" in caplog.text


def test_a_stop_entur_does_not_know_brings_no_notices_but_is_not_missing(store, provide, caplog):
    provide(UNKNOWN_ID)

    assert StoredEnturSituationsSource(store, [UNKNOWN_ID]).fetch(NOW) == []
    assert f"Entur doesn't know stop {UNKNOWN_ID}" in caplog.text


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = NOW

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return Clock.now

    monkeypatch.setattr(board_module, "datetime", FrozenDatetime)
    return Clock


def test_the_board_keeps_its_last_entur_notices_while_the_store_has_none(store, provide, clock, caplog):
    board = NoticeBoardClient(REGISTRATION, NoticeBoardSettings(postcode=None))
    board.sources = []  # only Entur's
    board.use_client_sources([StoredEnturSituationsSource(store, STOP_IDS)])
    provide(*STOP_IDS)
    shown = asyncio.run(board.update()).notices
    assert sorted(shown, key=lambda n: n.severity) == shown
    assert set(shown) == set(pinned("en"))

    store.flushall()  # the provider's keys expired
    clock.now += timedelta(minutes=2)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=board_module.__name__):
        assert asyncio.run(board.update()).notices == shown

    [record] = [r for r in caplog.records if r.name == board_module.__name__]
    assert record.getMessage().startswith("Entur disruptions: No situations in the store for Entur stops")
    assert not record.exc_info  # expected, so no traceback


def test_a_key_error_in_a_source_is_still_a_bug_with_its_traceback(clock, caplog):
    class Broken(NoticeSource):
        name = "Broken"
        refresh_interval = timedelta(minutes=1)

        def fetch(self, now):
            return {}["items"]

    board = NoticeBoardClient(REGISTRATION, NoticeBoardSettings(postcode=None))
    board.sources = []
    board.use_client_sources([Broken(requests.Session())])
    with pytest.raises(RuntimeError):
        asyncio.run(board.update())

    [record] = [r for r in caplog.records if r.name == board_module.__name__]
    assert record.exc_info is not None


def test_the_board_reads_the_store_each_time_it_draws(store, provide, clock):
    board = NoticeBoardClient(REGISTRATION, NoticeBoardSettings(postcode=None))
    board.sources = []
    board.use_client_sources([StoredEnturSituationsSource(store, [QUAY_ID])])
    with pytest.raises(RuntimeError, match="None of the notice sources could be reached"):
        asyncio.run(board.update())  # nothing stored yet

    provide(QUAY_ID)
    clock.now += StoredEnturSituationsSource.refresh_interval

    assert len(asyncio.run(board.update()).notices) == 2


def test_the_departures_client_hands_the_board_its_entur_stops_from_the_store(store):
    stops = (DepartureStop(source="entur", id=QUAY_ID), DepartureStop(source="tfl", id="490000123W"))

    sources = DeparturesClient(REGISTRATION, DeparturesSettings(stops)).notice_sources()

    [entur_source] = [s for s in sources if isinstance(s, StoredEnturSituationsSource)]
    assert (entur_source.store, entur_source.stop_ids) == (store, [QUAY_ID])
    assert entur_source.needs_network  # the store counts as reaching something, as Entur did
