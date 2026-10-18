"""Handing screens to the render pool, and back: the orchestrator's part in the migration to stateless renderers.

pi-dashboard's docs/stateless-renderers.md, step 6. The render pool (countdown-render-worker and -ticker, beside the
orchestrator in server/deploy/docker-compose.yml) draws the screens listed in `v1:render:screens`, a hash of device id
to slug, and drops a job for any other. Until the broker writes that hash itself (step 9), the orchestrator does, for
the screens in its POOL_SLUGS:

- **Handing over.** On every pass, a screen in POOL_SLUGS that's wanted on (pending, provisioned or failed) has its
  renderer stopped, keeping the container and its volumes, and is then written into the hash. Every pass, so a Redis
  restart, which empties the hash, is undone within one. It's reported provisioned: it's being drawn.
- **Handing back.** A screen in the hash that isn't to be pooled any more (its slug taken out of POOL_SLUGS, suspended,
  being deleted, or gone) is taken out of it. Its renderer is started again once no render of it is in flight: while
  its lease (`v1:render:lease:<device id>`) is live, it's held back for the next pass. As the workers' claim checks the
  hash atomically with taking the lease, once a screen is out of the hash no new render of it can start.
- A screen in POOL_SLUGS whose assignment has no device id (a broker before auth-broker #202) is logged and left on its
  own renderer.
- With POOL_SLUGS empty, nothing changes but one read of the (empty) hash per pass.
- Redis unreachable: the screens in POOL_SLUGS are left as they are, neither stopped nor started, and the others are
  reconciled as ever.

Redis is the host's (RENDERER_REDIS_URL, with REDIS_PASSWORD), which the renderers and the pool read too. The
orchestrator's image has no countdown-core, so the two keys it uses are spelled out here; a test pins them to
countdown_core.store.keys.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import redis
from redis.exceptions import RedisError

from countdown_orchestrator.models import ACTIVE, valid_slug

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from countdown_orchestrator.models import Assignment
    from countdown_orchestrator.runtime import Renderer

logger = logging.getLogger(__name__)

# countdown_core.store.keys.RENDER_SCREENS and render_lease.
SCREENS = "v1:render:screens"
TIMEOUT_S = 2


def lease(device_id: str) -> str:
    """Return the key of the lease a worker holds while it renders the screen (countdown_core's keys.render_lease)."""
    return f"v1:render:lease:{device_id}"


def parse_slugs(value: str) -> frozenset[str]:
    """Return the slugs in POOL_SLUGS: separated by commas or spaces."""
    return frozenset(slug for slug in re.split(r"[\s,]+", value) if slug)


@dataclass(frozen=True)
class PoolPlan:
    """What a pass does about the pool: which screens it hands over, and which renderers it mustn't start yet.

    `pooled` is the slug and device id of each screen the pool is to draw; `held` the slugs whose renderers mustn't be
    started (a render of it by the pool still in flight, or Redis unreachable, so it's not known).
    """

    pooled: Mapping[str, str] = field(default_factory=dict)
    held: frozenset[str] = frozenset()


NO_POOL = PoolPlan()


class Pool:
    """The render pool's screens, as the orchestrator keeps them: the ones in `slugs` (POOL_SLUGS), on `client`."""

    def __init__(self, client: redis.Redis, slugs: frozenset[str]) -> None:
        """Hand the screens named `slugs` to the pool, through the store `client` (decoding values as text)."""
        self.client = client
        self.slugs = slugs
        self._unreachable = False
        self._no_device_id: set[str] = set()

    @classmethod
    def from_env(cls) -> Pool | None:
        """Make one from POOL_SLUGS and the store RENDERER_REDIS_URL names; None without a store.

        Made with POOL_SLUGS empty too, so taking the last slug out of it still hands that screen back.
        """
        slugs = parse_slugs(os.environ.get("POOL_SLUGS", ""))
        url = os.environ.get("RENDERER_REDIS_URL", "")
        if not url:
            if slugs:
                logger.warning("POOL_SLUGS is set, but there's no store (RENDERER_REDIS_URL): no screen is pooled")
            return None
        for slug in sorted(slugs):
            if not valid_slug(slug):
                logger.warning("POOL_SLUGS: %r isn't a slug, so no screen has it", slug)
        logger.info("Screens on the render pool: %s", ", ".join(sorted(slugs)) or "none")
        client = redis.Redis.from_url(
            url,
            password=os.environ.get("REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_timeout=TIMEOUT_S,
            socket_connect_timeout=TIMEOUT_S,
        )
        return cls(client, slugs)

    def plan(self, wanted: Sequence[Assignment], existing: Mapping[str, Renderer]) -> PoolPlan:
        """Take the screens not to be pooled out of the hash, and say which the pass hands over and holds back."""
        pooled = {a.slug: a.device_id for a in wanted if self._to_pool(a)}
        try:
            current = self.client.hgetall(SCREENS)
            # A device listed under another slug than its own is stale too, and is written again as it should be.
            stale = {device_id: slug for device_id, slug in current.items() if pooled.get(slug) != device_id}
            if stale:
                self.client.hdel(SCREENS, *stale)
                logger.info("Took %s out of the render pool", ", ".join(sorted(stale.values())))
            # Starting a screen's own renderer waits for any render of it the pool has in flight.
            starting = [
                a
                for a in wanted
                if a.slug not in pooled
                and a.device_id
                and a.status in ACTIVE
                and not ((renderer := existing.get(a.slug)) is not None and renderer.running)
            ]
            held = self._leased(starting)
        except RedisError as e:
            if not self._unreachable:
                logger.warning(
                    "Couldn't reach the store for the render pool (%s); leaving the pool's screens as they are",
                    type(e).__name__,
                )
            self._unreachable = True
            return PoolPlan(held=self.slugs)
        if self._unreachable:
            logger.info("Reached the store for the render pool again")
            self._unreachable = False
        return PoolPlan(pooled, held)

    def publish(self, handed: Mapping[str, str]) -> None:
        """Write the screens handed over this pass (slug to device id) into the hash; a Redis error is logged."""
        if not handed:
            return
        try:
            self.client.hset(SCREENS, mapping={device_id: slug for slug, device_id in handed.items()})
        except RedisError as e:
            logger.warning("Couldn't write the render pool's screens (%s); trying again next pass", type(e).__name__)

    def _to_pool(self, assignment: Assignment) -> bool:
        """Whether the pool is to draw the assignment's screen: in POOL_SLUGS, wanted on, and with a device id."""
        if assignment.slug not in self.slugs or assignment.status not in ACTIVE or not valid_slug(assignment.slug):
            return False
        if not assignment.device_id:
            if assignment.slug not in self._no_device_id:
                logger.warning(
                    "%s is in POOL_SLUGS, but the broker lists no device id for it (an older broker?); "
                    "leaving it on its own renderer",
                    assignment.slug,
                )
                self._no_device_id.add(assignment.slug)
            return False
        self._no_device_id.discard(assignment.slug)
        return True

    def _leased(self, assignments: Sequence[Assignment]) -> frozenset[str]:
        """Return the slugs of those of `assignments` whose screen has a render in flight in the pool."""
        if not assignments:
            return frozenset()
        with self.client.pipeline(transaction=False) as pipe:
            for assignment in assignments:
                pipe.exists(lease(assignment.device_id))
            live = pipe.execute()
        return frozenset(a.slug for a, is_live in zip(assignments, live, strict=True) if is_live)
