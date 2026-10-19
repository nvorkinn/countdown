"""The render pool's job queue: which screens to draw next, one render per screen at a time.

docs/stateless-renderers.md ("The pool, the queue and the render function") is the design. Anything that wants a screen
drawn (the ticker on every minute, the broker on a config save or a pairing code, and a provider whose data turns
bursty)
calls enqueue(device id). A worker pops a job (next_job), claims it, renders the screen, and releases it. Three small
Lua scripts make enqueue, claim and release each atomic in Redis, over these keys (keys.py):

| Key | Kind | What it's for |
|---|---|---|
| `v1:render:jobs` | list | the job queue, of device ids |
| `v1:render:queued:<id>` | flag, QUEUED_TTL_MS | a job for the screen is in the queue; holds when it was queued |
| `v1:render:lease:<id>` | string with a token, LEASE_TTL_MS | a render for the screen is in flight |
| `v1:render:again:<id>` | flag, AGAIN_TTL_MS | a job came while one was in flight |
| `v1:render:screens` | hash, device id to slug | the screens the pool renders |

- **enqueue(id):** `SET queued NX`; only if that set it, `LPUSH jobs id`. So a screen is in the queue at most once,
  however many triggers fire.
- **claim(id), after the pop:** `DEL queued`. If the screen isn't in `v1:render:screens` (which auth-broker writes, for
  every paired device), the job is dropped. Otherwise `SET lease token NX`, and the worker renders; if the
  lease is taken, `SET again` instead, and the job is done.
- **release(id):** if the lease is still this worker's token, `DEL lease`; if `DEL again` removed one, enqueue(id).

So at most one render per screen is in flight, and a job that comes during a render leaves exactly one follow-up: the
claim and the release are atomic over the same keys, so either the job's claim sees the lease and flags `again` before
the release checks it, or it comes after the release and renders itself. Checking the screen is listed inside the claim
means that once the broker has taken a screen out of the hash (forgotten, or unpaired), no render of it can start.

A worker that dies mid-render loses its job and its follow-up: the lease and the flag lapse by themselves, and the next
minute's tick draws the screen. A worker that dies between the pop and the claim leaves the `queued` flag, which lapses
too (QUEUED_TTL_MS), so the screen is out of the queue for a minute at most. A Redis restart loses the queue, and the
next tick fills it again.

The times in the flags are Redis's own (TIME), so a job's wait (Claimed.waited_s) doesn't depend on whose clock queued
it.
"""

import secrets
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from countdown_api import scripts
from countdown_core.store import keys

if TYPE_CHECKING:
    from collections.abc import Iterable

    import redis

# The lease on a render in flight: longer than the worker's deadline for one render (20 s), so a render that's still
# going always holds it, and short enough that a worker that died frees its screen well before the next tick but one.
LEASE_TTL_MS = 30_000
# The follow-up flag: as long as the lease it's set beside.
AGAIN_TTL_MS = 30_000
# The queued flag: a minute, the ticker's interval. Only a worker dying between the pop and the claim leaves one behind.
# The store contract's, as auth-broker queues jobs with the same script and TTL.
QUEUED_TTL_MS = keys.RENDER_QUEUED_TTL_MS

# Redis's clock, in Unix milliseconds, as a string: the seconds and the milliseconds, put together as text so that the
# number doesn't go through Lua's floating point. The enqueue script starts with the same lines.
_NOW_MS = "local t = redis.call('TIME')\nlocal now_ms = t[1] .. string.format('%03d', math.floor(t[2] / 1000))\n"

# The store contract's enqueue script (countdown-api's lua/enqueue.lua), which auth-broker runs too. KEYS: queued, jobs.
# ARGV: the device id, the queued flag's TTL. Returns 1 if it queued the screen, 0 if it was queued already.
_ENQUEUE = scripts.ENQUEUE.source

# KEYS: queued, lease, again, screens. ARGV: the device id, the worker's token, the lease's TTL, the follow-up flag's
# TTL.
# Returns {what to do, the screen's slug, when it was queued, now}, the times in Unix ms ('' when unknown).
_CLAIM = (
    _NOW_MS
    + """
local queued_at = redis.call('GET', KEYS[1]) or ''
redis.call('DEL', KEYS[1])
local slug = redis.call('HGET', KEYS[4], ARGV[1])
if not slug then
  return {'dropped', '', queued_at, now_ms}
end
if redis.call('SET', KEYS[2], ARGV[2], 'NX', 'PX', ARGV[3]) then
  return {'render', slug, queued_at, now_ms}
end
redis.call('SET', KEYS[3], '1', 'PX', ARGV[4])
return {'again', slug, queued_at, now_ms}
"""
)

# KEYS: lease, again, queued, jobs. ARGV: the worker's token, the device id, the queued flag's TTL. Returns 1 if a job
# had come during the render (and so the screen is queued again), else 0.
_RELEASE = (
    _NOW_MS
    + """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DEL', KEYS[1])
end
if redis.call('DEL', KEYS[2]) == 0 then
  return 0
end
if redis.call('SET', KEYS[3], now_ms, 'NX', 'PX', ARGV[3]) then
  redis.call('LPUSH', KEYS[4], ARGV[2])
end
return 1
"""
)


class Claim(StrEnum):
    """What a worker does with a job it popped."""

    # Draw the screen: the worker holds its lease.
    RENDER = auto()
    # Nothing: the screen's render is in flight elsewhere, and a follow-up is flagged for when it's done.
    AGAIN = auto()
    # Nothing: the pool doesn't render this screen (it isn't in v1:render:screens).
    DROPPED = auto()


@dataclass(frozen=True)
class Claimed:
    """A claim's answer: what to do, the screen's slug (None if dropped), and how long the job waited in the queue.

    `token` is the lease's, to release it with; `waited_s` is None when the job's queued flag had gone (it lapsed, or
    whoever queued it didn't set one).
    """

    claim: Claim
    slug: str | None
    waited_s: float | None
    token: str


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def enqueue(client: redis.Redis, device_id: str) -> bool:
    """Queue a render of the screen, unless one is queued already; return whether this queued it."""
    script = client.register_script(_ENQUEUE)
    return bool(script(keys=[keys.render_queued(device_id), keys.RENDER_JOBS], args=[device_id, QUEUED_TTL_MS]))


def enqueue_all(client: redis.Redis, device_ids: Iterable[str]) -> int:
    """Queue a render of each screen not queued already, in one round trip; return how many this queued."""
    script = client.register_script(_ENQUEUE)
    with client.pipeline(transaction=False) as pipe:
        for device_id in device_ids:
            script(keys=[keys.render_queued(device_id), keys.RENDER_JOBS], args=[device_id, QUEUED_TTL_MS], client=pipe)
        return sum(bool(queued) for queued in pipe.execute())


def next_job(client: redis.Redis, timeout_s: int) -> str | None:
    """Wait up to `timeout_s` for a job, and return its device id; None if none came.

    `client`'s socket timeout must be longer than `timeout_s`, or the wait raises a TimeoutError.
    """
    popped = client.brpop([keys.RENDER_JOBS], timeout=timeout_s)
    return None if popped is None else _text(popped[1])


def claim(client: redis.Redis, device_id: str) -> Claimed:
    """Claim the job just popped for the screen: render it (holding its lease), leave a follow-up, or drop it."""
    token = secrets.token_hex(8)
    script = client.register_script(_CLAIM)
    what, slug, queued_at, now_ms = (
        _text(value)
        for value in script(
            keys=[
                keys.render_queued(device_id),
                keys.render_lease(device_id),
                keys.render_again(device_id),
                keys.RENDER_SCREENS,
            ],
            args=[device_id, token, LEASE_TTL_MS, AGAIN_TTL_MS],
        )
    )
    waited_s = (int(now_ms) - int(queued_at)) / 1000 if queued_at.isdigit() else None
    return Claimed(Claim(what), slug or None, waited_s, token)


def release(client: redis.Redis, device_id: str, token: str) -> bool:
    """Free the screen after its render, and queue its follow-up if a job came meanwhile; return whether one did."""
    script = client.register_script(_RELEASE)
    return bool(
        script(
            keys=[
                keys.render_lease(device_id),
                keys.render_again(device_id),
                keys.render_queued(device_id),
                keys.RENDER_JOBS,
            ],
            args=[token, device_id, QUEUED_TTL_MS],
        )
    )


def screens(client: redis.Redis) -> dict[str, str]:
    """Return the screens the pool renders: their slugs by device id."""
    return {_text(device_id): _text(slug) for device_id, slug in client.hgetall(keys.RENDER_SCREENS).items()}


def queued(client: redis.Redis) -> int:
    """Return how many jobs are waiting."""
    return client.llen(keys.RENDER_JOBS)
