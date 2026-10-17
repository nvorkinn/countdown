"""The providers' shared loop (countdown_providers.runner), against fakeredis: no real Redis or API."""

import asyncio
import json
from datetime import UTC, datetime

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from countdown_core.store import keys
from countdown_core.store.models import StopDepartures
from countdown_providers.config import DEFAULT_REDIS_URL, Settings
from countdown_providers.runner import ProviderRunner, next_tick
from countdown_providers.sharding import Shard, shard_of
from countdown_providers.weather.main import WeatherProvider

FETCHED_AT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
# Long enough that a test only gets a second cycle by nudging (or a short interval of its own).
NEVER_S = 3600


class FakeProvider:
    """Returns a StopDepartures for every id it's asked for, except those in `missing`; records each call.

    `fetched` is set once it has been called `enough` times, for the loop tests to wait on.
    """

    name = "fake"
    ttl_s = 120

    def __init__(self, interval_s: float = NEVER_S, missing: frozenset[str] = frozenset(), enough: int = 1):
        self.interval_s = interval_s
        self.missing = missing
        self.calls: list[list[str]] = []
        self.enough = enough
        self.fetched = asyncio.Event()

    def output_key(self, item_id):
        return keys.departures(item_id)

    async def fetch(self, ids):
        self.calls.append(ids)
        if len(self.calls) >= self.enough:
            self.fetched.set()
        return {
            item_id: StopDepartures(fetched_at=FETCHED_AT, stop_id=item_id, name=item_id, mode="bus")
            for item_id in ids
            if item_id not in self.missing
        }


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def subscribe(redis, *ids, source="fake"):
    asyncio.run(redis.sadd(keys.subscription(source), *ids))


# --- Sharding --------------------------------------------------------------------


def test_shard_of_is_deterministic():
    # Fixed values, so they can't come from a per-process hash (Python's hash() of a str changes from run to run), and
    # a change to the hash, which would move ids between shards, shows up here.
    ids = ("490000123W", "940GZZLUKNG", "NSR:StopPlace:58366", "london-gb-2643743")
    assert [shard_of(item_id, 4) for item_id in ids] == [1, 3, 1, 1]
    assert shard_of("490000123W", 1000) == 441
    assert shard_of("490000123W", 1) == 0


def test_shard_of_spreads_ids():
    counts = [0, 0, 0, 0]
    for n in range(1000):
        counts[shard_of(f"stop-{n}", 4)] += 1
    assert all(200 <= count <= 300 for count in counts), counts


def test_every_id_is_owned_by_exactly_one_shard():
    shards = [Shard(index, 3) for index in range(3)]
    for n in range(100):
        assert sum(shard.owns(f"stop-{n}") for shard in shards) == 1


@pytest.mark.parametrize(("index", "count"), [(0, 0), (1, 1), (-1, 2)])
def test_bad_shard_is_refused(index, count):
    with pytest.raises(ValueError, match="SHARD_"):
        Shard(index, count)


def test_settings_from_env():
    settings = Settings.from_env(
        {"REDIS_URL": "redis://example:1/2", "REDIS_PASSWORD": "pw", "SHARD_INDEX": "1", "SHARD_COUNT": "3"}
    )
    assert settings == Settings("redis://example:1/2", "pw", Shard(1, 3))
    assert "pw" not in repr(settings)


def test_settings_defaults():
    assert Settings.from_env({"REDIS_PASSWORD": ""}) == Settings(DEFAULT_REDIS_URL, None, Shard(0, 1))


# --- Ticks -----------------------------------------------------------------------


def test_next_tick_is_epoch_aligned():
    assert next_tick(1000.0, 30) == 1020.0
    assert next_tick(1019.9, 30) == 1020.0
    # Exactly on a tick: that one's just run, so the next.
    assert next_tick(1020.0, 30) == 1050.0


# --- A cycle ---------------------------------------------------------------------


def test_cycle_writes_every_subscribed_id_with_its_ttl(redis):
    subscribe(redis, "a", "b", "c")
    provider = FakeProvider()

    assert asyncio.run(ProviderRunner(provider, redis).run_cycle()) == 3

    for stop_id in ("a", "b", "c"):
        stored = StopDepartures.model_validate_json(asyncio.run(redis.get(keys.departures(stop_id))))
        assert stored.stop_id == stop_id
        assert stored.departures == []  # nothing due is still written
        assert 0 < asyncio.run(redis.ttl(keys.departures(stop_id))) <= 120


def test_cycle_with_no_subscriptions_writes_nothing(redis):
    provider = FakeProvider()
    assert asyncio.run(ProviderRunner(provider, redis).run_cycle()) == 0
    assert provider.calls == []


def test_cycle_fetches_in_batches(redis):
    subscribe(redis, *[f"s{n}" for n in range(5)])
    provider = FakeProvider()
    asyncio.run(ProviderRunner(provider, redis, batch_size=2).run_cycle())
    assert [len(call) for call in provider.calls] == [2, 2, 1]


def test_cycle_only_fetches_its_shard(redis):
    ids = [f"stop-{n}" for n in range(20)]
    subscribe(redis, *ids)
    shard = Shard(1, 3)
    provider = FakeProvider()

    asyncio.run(ProviderRunner(provider, redis, shard=shard).run_cycle())

    fetched = [item_id for call in provider.calls for item_id in call]
    assert fetched == sorted(item_id for item_id in ids if shard.owns(item_id))
    assert fetched  # the test means something
    for item_id in ids:
        assert (asyncio.run(redis.exists(keys.departures(item_id))) == 1) == shard.owns(item_id)


def test_cycle_skips_ids_the_provider_did_not_return(redis):
    subscribe(redis, "good", "unknown")
    asyncio.run(ProviderRunner(FakeProvider(missing=frozenset({"unknown"})), redis).run_cycle())
    assert asyncio.run(redis.exists(keys.departures("good"))) == 1
    assert asyncio.run(redis.exists(keys.departures("unknown"))) == 0


def test_cycle_ignores_ids_it_did_not_ask_for(redis):
    subscribe(redis, "asked")

    class Overeager(FakeProvider):
        async def fetch(self, ids):
            return await super().fetch([*ids, "not-asked"])

    asyncio.run(ProviderRunner(Overeager(), redis).run_cycle())
    assert asyncio.run(redis.exists(keys.departures("not-asked"))) == 0


def test_cycle_survives_a_provider_exception(redis, caplog):
    subscribe(redis, "a", "b", "c")
    provider = FakeProvider()
    original = provider.fetch

    async def fail_first_batch(ids):
        if not provider.calls:
            provider.calls.append(ids)
            msg = "API down"
            raise RuntimeError(msg)
        return await original(ids)

    provider.fetch = fail_first_batch

    assert asyncio.run(ProviderRunner(provider, redis, batch_size=2).run_cycle()) == 1
    assert asyncio.run(redis.exists(keys.departures("c"))) == 1
    assert "couldn't fetch 2 ids" in caplog.text


def test_unreachable_redis_is_logged_not_raised(caplog):
    class Unreachable(fakeredis.FakeAsyncRedis):
        async def smembers(self, name):
            msg = "Connection refused"
            raise RedisConnectionError(msg)

    asyncio.run(ProviderRunner(FakeProvider(), Unreachable(decode_responses=True))._try_cycle())
    assert "couldn't reach Redis" in caplog.text


# --- The loop --------------------------------------------------------------------


# A test that waits this long has failed; none takes more than a fraction of a second.
WAIT_S = 2


async def run_until_fetched(runner):
    """Start runner.run(), wait until its provider has been called `enough` times, then stop it and let it finish."""
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(runner.provider.fetched.wait(), WAIT_S)
    finally:
        runner.stop()
        await asyncio.wait_for(task, WAIT_S)


def test_run_does_a_cycle_at_once_and_stops_when_asked(redis):
    subscribe(redis, "a")
    provider = FakeProvider()
    asyncio.run(run_until_fetched(ProviderRunner(provider, redis)))
    assert provider.calls == [["a"]]


def test_run_cycles_on_each_tick(redis):
    subscribe(redis, "a")
    provider = FakeProvider(interval_s=0.02, enough=3)
    asyncio.run(run_until_fetched(ProviderRunner(provider, redis)))
    assert provider.calls == [["a"]] * 3


def test_run_wakes_early_on_a_subscription_change(redis):
    provider = FakeProvider()  # ticks hourly: only a nudge can run the second cycle
    runner = ProviderRunner(provider, redis)

    async def scenario():
        task = asyncio.create_task(runner.run())
        # Wait for the listener to subscribe (the first cycle, with nothing subscribed, fetches nothing). Redis has
        # no event for that, so poll.
        async with asyncio.timeout(WAIT_S):
            while not (await redis.pubsub_numsub(keys.SUB_CHANGED))[0][1]:  # noqa: ASYNC110 -- nothing to wait on
                await asyncio.sleep(0.01)
        await redis.sadd(keys.subscription("fake"), "new")
        await redis.publish(keys.SUB_CHANGED, "")
        await asyncio.wait_for(provider.fetched.wait(), WAIT_S)
        runner.stop()
        await asyncio.wait_for(task, WAIT_S)

    asyncio.run(scenario())
    assert provider.calls == [["new"]]
    assert asyncio.run(redis.exists(keys.departures("new"))) == 1


def test_stop_finishes_the_current_cycle(redis):
    subscribe(redis, "a", "b")
    started = asyncio.Event()
    release = asyncio.Event()

    class Slow(FakeProvider):
        async def fetch(self, ids):
            started.set()
            await release.wait()
            return await super().fetch(ids)

    provider = Slow()
    runner = ProviderRunner(provider, redis)

    async def scenario():
        task = asyncio.create_task(runner.run())
        await asyncio.wait_for(started.wait(), WAIT_S)
        runner.stop()  # mid-cycle, as SIGTERM would
        release.set()
        await asyncio.wait_for(task, WAIT_S)

    asyncio.run(scenario())
    # The cycle it was in still wrote its keys, and no other cycle began.
    assert asyncio.run(redis.exists(keys.departures("a"))) == 1
    assert len(provider.calls) == 1


def test_written_value_is_the_models_json(redis):
    subscribe(redis, "a")
    asyncio.run(ProviderRunner(FakeProvider(), redis).run_cycle())
    assert json.loads(asyncio.run(redis.get(keys.departures("a")))) == {
        "fetched_at": "2026-10-17T12:00:00Z",
        "stop_id": "a",
        "name": "a",
        "mode": "bus",
        "stop_letter": None,
        "departures": [],
    }


def test_weather_stub_writes_nothing(redis):
    subscribe(redis, "london-gb-2643743", source=keys.WEATHER)
    provider = WeatherProvider()
    assert asyncio.run(ProviderRunner(provider, redis).run_cycle()) == 0
    assert provider.output_key("london-gb-2643743") == "v1:weather:london-gb-2643743"
