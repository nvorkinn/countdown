"""The orchestrator handing the screens in POOL_SLUGS to the render pool, and back (countdown_orchestrator.pool).

Through reconcile(), with the fake broker and runtime of test_orchestrator and the pool's keys in a fake Redis.
"""

import logging

import fakeredis
import pytest
import redis
import responses
from test_orchestrator import BROKER_URL, LIST_URL, FakeBroker
from test_orchestrator import FakeRuntime as Runtime

from countdown_core.store import keys
from countdown_orchestrator import main, pool
from countdown_orchestrator.broker import OrchestratorBroker
from countdown_orchestrator.models import Assignment, Status
from countdown_orchestrator.pool import Pool, parse_slugs
from countdown_orchestrator.reconcile import reconcile
from countdown_orchestrator.runtime import Renderer

DEVICE = "bb8a8fbedb71"
SLUG = "nvorkinn-test"


class FakeRuntime(Runtime):
    """As test_orchestrator's, but a stopped renderer is listed as stopped, as Docker lists it."""

    def stop(self, slug):
        super().stop(slug)
        if slug in self.existing:
            self.existing[slug] = Renderer(slug, running=False, up_to_date=self.existing[slug].up_to_date)


def _assignment(slug=SLUG, status=Status.PROVISIONED, device_id=DEVICE, id_="a1") -> Assignment:
    return Assignment(id=id_, slug=slug, claim_token=None, status=status, device_id=device_id)


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


def _pool(redis_client, *slugs) -> Pool:
    return Pool(redis_client, frozenset(slugs))


def _screens(redis_client) -> dict[str, str]:
    return redis_client.hgetall(keys.RENDER_SCREENS)


def test_the_keys_are_the_render_queues():
    assert pool.SCREENS == keys.RENDER_SCREENS
    assert pool.lease(DEVICE) == keys.render_lease(DEVICE)


@pytest.mark.parametrize(
    ("value", "slugs"),
    [("", set()), ("nvorkinn-test", {SLUG}), (" a1, b2  c3,,", {"a1", "b2", "c3"})],
)
def test_pool_slugs_are_separated_by_commas_or_spaces(value, slugs):
    assert parse_slugs(value) == slugs


def test_with_pool_slugs_empty_nothing_changes(redis_client):
    wanted = [_assignment(), _assignment("lisa", Status.PENDING, "0f9e8d7c6b5a", "a2")]
    with_pool = FakeBroker(*wanted), FakeRuntime(Renderer(SLUG, running=True))
    without = FakeBroker(*wanted), FakeRuntime(Renderer(SLUG, running=True))

    reconcile(*with_pool, pool=_pool(redis_client))
    reconcile(*without)

    assert (with_pool[0].reports, with_pool[1].ensured, with_pool[1].stopped) == (
        without[0].reports,
        without[1].ensured,
        without[1].stopped,
    )
    assert _screens(redis_client) == {}


def test_a_pooled_screens_renderer_is_stopped_and_kept_and_the_screen_written_into_the_hash(redis_client, caplog):
    broker, runtime = FakeBroker(_assignment(status=Status.PENDING)), FakeRuntime(Renderer(SLUG, running=True))

    with caplog.at_level(logging.INFO, logger="countdown_orchestrator.reconcile"):
        reconcile(broker, runtime, pool=_pool(redis_client, SLUG))

    assert runtime.stopped == [SLUG]
    assert runtime.removed == []
    assert runtime.ensured == []
    assert _screens(redis_client) == {DEVICE: SLUG}
    assert broker.reports == [(SLUG, Status.PROVISIONED, "")]
    assert caplog.messages == [f"Handed {SLUG} to the render pool; its renderer is stopped, and kept with its volumes"]


def test_the_hash_is_written_on_every_pass_so_a_redis_restart_is_undone(redis_client):
    broker, runtime = FakeBroker(_assignment()), FakeRuntime(Renderer(SLUG, running=True))
    the_pool = _pool(redis_client, SLUG)
    reconcile(broker, runtime, pool=the_pool)
    redis_client.flushall()  # Redis restarted

    reconcile(broker, runtime, pool=the_pool)

    assert _screens(redis_client) == {DEVICE: SLUG}
    assert runtime.stopped == [SLUG]  # once: it stays stopped
    assert runtime.ensured == []


def test_a_pooled_screen_with_no_renderer_yet_is_just_listed(redis_client):
    broker, runtime = FakeBroker(_assignment(status=Status.PENDING)), FakeRuntime()

    reconcile(broker, runtime, pool=_pool(redis_client, SLUG))

    assert (runtime.stopped, runtime.ensured) == ([], [])
    assert _screens(redis_client) == {DEVICE: SLUG}


def test_without_a_device_id_from_the_broker_the_screen_stays_on_its_renderer(redis_client, caplog):
    broker = FakeBroker(_assignment(device_id=None))
    runtime = FakeRuntime(Renderer(SLUG, running=False))
    the_pool = _pool(redis_client, SLUG)

    with caplog.at_level(logging.WARNING, logger="countdown_orchestrator.pool"):
        reconcile(broker, runtime, pool=the_pool)
        reconcile(broker, runtime, pool=the_pool)

    assert runtime.ensured == [SLUG]
    assert _screens(redis_client) == {}
    assert caplog.messages == [  # once, not every pass
        (
            f"{SLUG} is in POOL_SLUGS, but the broker lists no device id for it (an older broker?); "
            "leaving it on its own renderer"
        )
    ]


def test_rollback_takes_the_screen_out_and_waits_for_its_lease_before_starting_its_renderer(redis_client):
    broker, runtime = FakeBroker(_assignment()), FakeRuntime(Renderer(SLUG, running=True))
    reconcile(broker, runtime, pool=_pool(redis_client, SLUG))
    redis_client.set(keys.render_lease(DEVICE), "a-workers-token", px=30_000)  # a render in flight
    rolled_back = _pool(redis_client)  # the slug taken out of POOL_SLUGS

    reconcile(broker, runtime, pool=rolled_back)

    assert _screens(redis_client) == {}
    assert runtime.ensured == []  # not while the pool may still be drawing it

    redis_client.delete(keys.render_lease(DEVICE))  # released, or lapsed
    reconcile(broker, runtime, pool=rolled_back)

    assert runtime.ensured == [SLUG]  # started again (on the current image, with its volumes)


def test_rollback_with_no_render_in_flight_starts_the_renderer_in_the_same_pass(redis_client):
    redis_client.hset(keys.RENDER_SCREENS, DEVICE, SLUG)
    broker, runtime = FakeBroker(_assignment()), FakeRuntime(Renderer(SLUG, running=False))

    reconcile(broker, runtime, pool=_pool(redis_client))

    assert _screens(redis_client) == {}
    assert runtime.ensured == [SLUG]


@pytest.mark.parametrize("status", [Status.SUSPENDED, Status.DELETING])
def test_a_suspended_or_deleted_screen_leaves_the_pool(redis_client, status):
    redis_client.hset(keys.RENDER_SCREENS, DEVICE, SLUG)
    device_id = None if status == Status.DELETING else DEVICE
    broker = FakeBroker(_assignment(status=status, device_id=device_id))
    runtime = FakeRuntime(Renderer(SLUG, running=False))

    reconcile(broker, runtime, pool=_pool(redis_client, SLUG))

    assert _screens(redis_client) == {}
    assert runtime.ensured == []
    assert runtime.removed == ([SLUG] if status == Status.DELETING else [])


def test_a_screen_nobody_wants_any_more_leaves_the_pool(redis_client):
    redis_client.hset(keys.RENDER_SCREENS, mapping={DEVICE: SLUG, "0f9e8d7c6b5a": "gone"})

    reconcile(FakeBroker(_assignment()), FakeRuntime(), pool=_pool(redis_client, SLUG))

    assert _screens(redis_client) == {DEVICE: SLUG}


def test_a_renderer_that_wont_stop_isnt_handed_over(redis_client):
    class Stuck(FakeRuntime):
        def stop(self, slug):
            msg = "docker said no"
            raise RuntimeError(msg)

    broker, runtime = FakeBroker(_assignment()), Stuck(Renderer(SLUG, running=True))

    reconcile(broker, runtime, pool=_pool(redis_client, SLUG))

    assert _screens(redis_client) == {}
    assert broker.reports == [(SLUG, Status.FAILED, "RuntimeError: docker said no")]


def test_with_redis_unreachable_the_pooled_screens_are_left_and_the_rest_reconciled(redis_client, monkeypatch, caplog):
    def down(*args, **kwargs):
        raise redis.exceptions.ConnectionError

    monkeypatch.setattr(redis_client, "hgetall", down)
    broker = FakeBroker(_assignment(), _assignment("lisa", Status.PENDING, "0f9e8d7c6b5a", "a2"))
    runtime = FakeRuntime(Renderer(SLUG, running=True))
    the_pool = _pool(redis_client, SLUG)

    with caplog.at_level(logging.WARNING, logger="countdown_orchestrator.pool"):
        reconcile(broker, runtime, pool=the_pool)
        reconcile(broker, runtime, pool=the_pool)

    assert runtime.stopped == []
    assert runtime.ensured == ["lisa"]
    assert caplog.messages == [  # once, not every pass
        "Couldn't reach the store for the render pool (ConnectionError); leaving the pool's screens as they are"
    ]


def test_from_env_reads_pool_slugs_and_the_renderers_store(monkeypatch):
    monkeypatch.setenv("POOL_SLUGS", "nvorkinn-test, lisa")
    monkeypatch.setenv("RENDERER_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")

    made = Pool.from_env()

    assert made.slugs == {SLUG, "lisa"}
    assert made.client.connection_pool.connection_kwargs["password"] == "pw"
    assert made.client.connection_pool.connection_kwargs["port"] == 6379


def test_from_env_is_made_with_pool_slugs_empty_so_the_last_screen_can_be_handed_back(monkeypatch):
    monkeypatch.delenv("POOL_SLUGS", raising=False)
    monkeypatch.setenv("RENDERER_REDIS_URL", "redis://127.0.0.1:6379/0")

    assert Pool.from_env().slugs == frozenset()


def test_without_a_store_there_is_no_pool(monkeypatch, caplog):
    monkeypatch.setenv("POOL_SLUGS", SLUG)
    monkeypatch.setenv("RENDERER_REDIS_URL", "")

    with caplog.at_level(logging.WARNING, logger="countdown_orchestrator.pool"):
        assert Pool.from_env() is None
    assert caplog.messages == ["POOL_SLUGS is set, but there's no store (RENDERER_REDIS_URL): no screen is pooled"]


@responses.activate
def test_the_broker_client_reads_the_device_id_when_its_there():
    responses.get(
        LIST_URL,
        json={
            "assignments": [
                {"id": "a1", "slug": SLUG, "claim_token": None, "status": "provisioned", "device_id": DEVICE},
                {"id": "a2", "slug": "lisa", "claim_token": None, "status": "provisioned"},  # an older broker
            ]
        },
    )

    first, second = OrchestratorBroker(BROKER_URL, "t").assignments()

    assert (first.device_id, second.device_id) == (DEVICE, None)


def test_the_orchestrators_entry_point_hands_its_pool_to_every_pass(monkeypatch):
    monkeypatch.setenv("BROKER_URL", BROKER_URL)
    monkeypatch.setenv("ORCHESTRATOR_TOKEN", "t")
    monkeypatch.setenv("RENDERER_IMAGE", "ghcr.io/x/countdown-server:1")
    monkeypatch.setenv("RENDERER_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("POOL_SLUGS", SLUG)
    monkeypatch.setattr(main, "DockerRuntime", lambda **kwargs: FakeRuntime())
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    passes = []

    class Done(Exception):  # noqa: N818 -- not an error: how the test ends the loop
        pass

    def one_pass(broker, runtime, max_renderers, the_pool):
        passes.append(the_pool)

    def stop(seconds):
        raise Done

    monkeypatch.setattr(main, "reconcile", one_pass)
    monkeypatch.setattr(main.time, "sleep", stop)

    with pytest.raises(Done):
        main.run()

    [the_pool] = passes
    assert the_pool.slugs == {SLUG}
