"""The render pool's job queue (store.render_queue): its three scripts, against fakeredis and a real Redis.

The real Redis is a redis-server of the test's own, on a free port, when one is installed (CI installs it); its tests
are skipped otherwise. Every test runs on both, so the Lua is checked by the Redis that runs it in production too.
The enqueue script is the store contract's (countdown_api.scripts.ENQUEUE), which the broker runs to queue jobs itself,
and its render_queue.json (countdown_api.fixtures) pins the script's SHA1 and keys: both repositories' tests read it.
"""

import hashlib
import shutil
import socket
import subprocess
import threading
import time
from collections import Counter

import fakeredis
import pytest
import redis

from countdown_api import fixtures, scripts
from countdown_core.store import keys, render_queue
from countdown_core.store.render_queue import Claim

DEVICE = "a1b2c3d4e5f6"
OTHER = "0f9e8d7c6b5a"
# The enqueue script's SHA1 and keys, as the store contract pins them for both repositories.
QUEUE = fixtures.load("render_queue.json")
# The screens as countdown-server writes them (its broker/services/render_screens.py), the same file as its tests read.
SCREENS = fixtures.load("render_screens.json")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def redis_server():
    """Start a redis-server of the tests' own, with a password and no persistence; skip if there's none installed."""
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("no redis-server installed")
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603 -- a fixed binary and arguments
        [binary, "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no", "--requirepass", "pw"],
        stdout=subprocess.DEVNULL,
    )
    client = redis.Redis(port=port, password="pw", decode_responses=True)
    for _ in range(100):
        try:
            client.ping()
            break
        except redis.ConnectionError:
            time.sleep(0.05)
    yield f"redis://127.0.0.1:{port}/0"
    process.terminate()
    process.wait(5)


@pytest.fixture(params=["fakeredis", "redis-server"])
def client(request):
    """Return a client decoding values as text, on fakeredis or on the real Redis (emptied first)."""
    if request.param == "fakeredis":
        return fakeredis.FakeRedis(decode_responses=True)
    url = request.getfixturevalue("redis_server")
    real = redis.Redis.from_url(url, password="pw", decode_responses=True)
    real.flushall()
    return real


@pytest.fixture
def pooled(client):
    """Put DEVICE in the pool (but not OTHER)."""
    client.hset(keys.RENDER_SCREENS, DEVICE, "nvorkinn-test")
    return client


def _pop(client) -> str | None:
    return render_queue.next_job(client, 1)


def test_the_keys_are_the_designs():
    assert keys.RENDER_JOBS == "v1:render:jobs"
    assert keys.RENDER_SCREENS == "v1:render:screens"
    assert keys.render_queued(DEVICE) == f"v1:render:queued:{DEVICE}"
    assert keys.render_lease(DEVICE) == f"v1:render:lease:{DEVICE}"
    assert keys.render_again(DEVICE) == f"v1:render:again:{DEVICE}"


def test_the_enqueue_script_and_its_keys_are_the_store_contracts():
    # Its SHA1 is what EVALSHA calls it by; countdown-server runs the same script from the contract.
    script = render_queue._ENQUEUE
    assert script == scripts.ENQUEUE.source
    assert hashlib.sha1(script.encode(), usedforsecurity=False).hexdigest() == QUEUE["enqueue_sha1"]
    # The claim's and the release's clocks are the enqueue's, so a job's wait is measured on one clock.
    assert script.startswith(render_queue._NOW_MS)
    assert QUEUE["jobs_key"] == keys.RENDER_JOBS
    assert QUEUE["queued_key"] == keys.render_queued(QUEUE["device_id"])
    assert QUEUE["queued_ttl_ms"] == render_queue.QUEUED_TTL_MS


def test_the_screens_auth_broker_writes_are_the_ones_the_pool_reads(client):
    assert SCREENS["key"] == keys.RENDER_SCREENS
    client.hset(SCREENS["key"], mapping=SCREENS["screens"])

    assert render_queue.screens(client) == SCREENS["screens"]
    render_queue.enqueue(client, DEVICE)
    assert render_queue.claim(client, _pop(client)).claim is not Claim.DROPPED  # a listed device is drawn


def test_redis_knows_the_enqueue_script_by_the_fixtures_sha1(client):
    render_queue.enqueue(client, DEVICE)

    assert client.script_exists(QUEUE["enqueue_sha1"]) == [True]


def test_a_screen_is_in_the_queue_at_most_once_however_many_triggers_fire(pooled):
    queued = [render_queue.enqueue(pooled, DEVICE) for _ in range(5)]

    assert queued == [True, False, False, False, False]
    assert render_queue.queued(pooled) == 1
    assert 0 < pooled.pttl(keys.render_queued(DEVICE)) <= render_queue.QUEUED_TTL_MS


def test_enqueue_all_queues_each_screen_not_queued_already(pooled):
    render_queue.enqueue(pooled, DEVICE)

    assert render_queue.enqueue_all(pooled, [DEVICE, OTHER]) == 1
    assert render_queue.enqueue_all(pooled, []) == 0
    assert pooled.lrange(keys.RENDER_JOBS, 0, -1) == [OTHER, DEVICE]  # first in, first out


def test_a_claim_takes_the_lease_and_says_how_long_the_job_waited(pooled):
    render_queue.enqueue(pooled, DEVICE)
    time.sleep(0.02)

    claimed = render_queue.claim(pooled, _pop(pooled))

    assert (claimed.claim, claimed.slug) == (Claim.RENDER, "nvorkinn-test")
    assert 0.015 <= claimed.waited_s < 5
    assert pooled.get(keys.render_lease(DEVICE)) == claimed.token
    assert 0 < pooled.pttl(keys.render_lease(DEVICE)) <= render_queue.LEASE_TTL_MS
    assert not pooled.exists(keys.render_queued(DEVICE))


def test_one_render_per_screen_at_a_time(pooled):
    render_queue.enqueue(pooled, DEVICE)
    first = render_queue.claim(pooled, _pop(pooled))
    render_queue.enqueue(pooled, DEVICE)

    second = render_queue.claim(pooled, _pop(pooled))

    assert (first.claim, second.claim) == (Claim.RENDER, Claim.AGAIN)
    assert pooled.get(keys.render_lease(DEVICE)) == first.token  # still the first's
    assert pooled.exists(keys.render_again(DEVICE))


def test_jobs_during_a_render_leave_exactly_one_follow_up(pooled):
    render_queue.enqueue(pooled, DEVICE)
    rendering = render_queue.claim(pooled, _pop(pooled))
    # Ticks and nudges while it renders, each popped by another worker.
    for _ in range(5):
        render_queue.enqueue(pooled, DEVICE)
        assert render_queue.claim(pooled, _pop(pooled)).claim is Claim.AGAIN

    assert render_queue.release(pooled, DEVICE, rendering.token) is True

    assert pooled.lrange(keys.RENDER_JOBS, 0, -1) == [DEVICE]
    follow_up = render_queue.claim(pooled, _pop(pooled))
    assert follow_up.claim is Claim.RENDER
    assert render_queue.release(pooled, DEVICE, follow_up.token) is False
    assert render_queue.queued(pooled) == 0


def test_a_job_still_queued_when_the_render_ends_is_the_one_follow_up(pooled):
    render_queue.enqueue(pooled, DEVICE)
    rendering = render_queue.claim(pooled, _pop(pooled))
    render_queue.enqueue(pooled, DEVICE)  # not popped before the render ends
    render_queue.enqueue(pooled, DEVICE)  # popped, but flagged again
    render_queue.claim(pooled, _pop(pooled))
    render_queue.enqueue(pooled, DEVICE)  # queued once more

    render_queue.release(pooled, DEVICE, rendering.token)

    # The flag's follow-up coalesces with the job already queued.
    assert pooled.lrange(keys.RENDER_JOBS, 0, -1) == [DEVICE]


def test_a_job_after_the_render_renders_itself_and_leaves_no_follow_up(pooled):
    render_queue.enqueue(pooled, DEVICE)
    rendering = render_queue.claim(pooled, _pop(pooled))
    render_queue.release(pooled, DEVICE, rendering.token)
    render_queue.enqueue(pooled, DEVICE)

    after = render_queue.claim(pooled, _pop(pooled))

    assert after.claim is Claim.RENDER
    assert render_queue.release(pooled, DEVICE, after.token) is False
    assert render_queue.queued(pooled) == 0


def test_the_lease_lapses_so_a_worker_that_died_mid_render_frees_its_screen(pooled, monkeypatch):
    monkeypatch.setattr(render_queue, "LEASE_TTL_MS", 150)
    monkeypatch.setattr(render_queue, "AGAIN_TTL_MS", 150)
    render_queue.enqueue(pooled, DEVICE)
    died = render_queue.claim(pooled, _pop(pooled))  # and never released
    render_queue.enqueue(pooled, DEVICE)
    assert render_queue.claim(pooled, _pop(pooled)).claim is Claim.AGAIN

    time.sleep(0.3)
    render_queue.enqueue(pooled, DEVICE)  # the next tick
    recovered = render_queue.claim(pooled, _pop(pooled))

    assert recovered.claim is Claim.RENDER
    assert not pooled.exists(keys.render_again(DEVICE))  # the dead render's follow-up lapsed with it
    # The dead worker, had it come back, can't free the new render's lease.
    render_queue.release(pooled, DEVICE, died.token)
    assert pooled.get(keys.render_lease(DEVICE)) == recovered.token


def test_the_queued_flag_lapses_so_a_worker_that_died_after_the_pop_frees_its_screen(pooled, monkeypatch):
    monkeypatch.setattr(render_queue, "QUEUED_TTL_MS", 150)
    render_queue.enqueue(pooled, DEVICE)
    _pop(pooled)  # and died before the claim
    assert render_queue.enqueue(pooled, DEVICE) is False

    time.sleep(0.3)

    assert render_queue.enqueue(pooled, DEVICE) is True


def test_a_job_for_a_screen_the_pool_doesnt_render_is_dropped(pooled):
    render_queue.enqueue(pooled, OTHER)

    claimed = render_queue.claim(pooled, _pop(pooled))

    assert (claimed.claim, claimed.slug) == (Claim.DROPPED, None)
    assert not pooled.exists(keys.render_lease(OTHER), keys.render_again(OTHER), keys.render_queued(OTHER))


def test_a_job_with_no_queued_flag_has_no_wait(pooled):
    pooled.lpush(keys.RENDER_JOBS, DEVICE)  # as a trigger that only pushes might

    assert render_queue.claim(pooled, _pop(pooled)).waited_s is None


def test_no_job_is_none_after_the_wait(pooled):
    assert render_queue.next_job(pooled, 1) is None


def test_the_screens_are_the_hash(pooled):
    assert render_queue.screens(pooled) == {DEVICE: "nvorkinn-test"}


def test_workers_side_by_side_never_render_a_screen_twice_at_once_and_draw_after_the_last_job(redis_server):
    """Run four workers and a stream of jobs for three screens on a real Redis.

    At most one render per screen is in flight at a time, and each screen is drawn after the last job for it came.
    """
    client = redis.Redis.from_url(redis_server, password="pw", decode_responses=True, socket_timeout=5)
    client.flushall()
    devices = [f"device{i}" for i in range(3)]
    client.hset(keys.RENDER_SCREENS, mapping={device: f"screen-{device}" for device in devices})
    lock = threading.Lock()
    in_flight: Counter[str] = Counter()
    most: Counter[str] = Counter()
    started: dict[str, list[float]] = {device: [] for device in devices}
    done = threading.Event()

    def worker():
        own = redis.Redis.from_url(redis_server, password="pw", decode_responses=True, socket_timeout=5)
        while not done.is_set():
            device = render_queue.next_job(own, 1)
            if device is None:
                continue
            claimed = render_queue.claim(own, device)
            if claimed.claim is not Claim.RENDER:
                continue
            with lock:
                in_flight[device] += 1
                most[device] = max(most[device], in_flight[device])
                started[device].append(time.monotonic())
            time.sleep(0.01)
            with lock:
                in_flight[device] -= 1
            render_queue.release(own, device, claimed.token)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    last_job = {}
    for i in range(300):
        device = devices[i % 3]
        render_queue.enqueue(client, device)
        last_job[device] = time.monotonic()
        time.sleep(0.001)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (
        render_queue.queued(client) or any(client.exists(keys.render_lease(d)) for d in devices)
    ):
        time.sleep(0.05)
    done.set()
    for thread in threads:
        thread.join(5)

    assert all(most[device] == 1 for device in devices)
    assert all(started[device][-1] >= last_job[device] for device in devices)
