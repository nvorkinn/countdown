"""The settings every provider reads from its environment. A provider's own (TFL_APP_KEY...) are read by its main()."""

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from countdown_providers.sharding import Shard

if TYPE_CHECKING:
    from collections.abc import Mapping

# Redis runs on the providers' host (deployed by countdown-server), so host networking reaches it here.
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


@dataclass(frozen=True)
class Settings:
    """REDIS_URL, REDIS_PASSWORD, SHARD_INDEX and SHARD_COUNT."""

    redis_url: str = DEFAULT_REDIS_URL
    # Separate from the URL so the secret needn't be spliced into one. None: the URL's own, or none.
    redis_password: str | None = field(default=None, repr=False)
    shard: Shard = field(default_factory=Shard)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        """Read the settings from `env`; an unset or empty variable takes its default. Raises on a bad shard."""
        return cls(
            redis_url=env.get("REDIS_URL") or DEFAULT_REDIS_URL,
            redis_password=env.get("REDIS_PASSWORD") or None,
            shard=Shard(int(env.get("SHARD_INDEX") or 0), int(env.get("SHARD_COUNT") or 1)),
        )

    def redis(self) -> Redis:
        """Make a Redis client. It connects on first use, so this never fails for an unreachable Redis."""
        # Only pass the password if there is one: password=None would override one in the URL.
        # decode_responses: ids and keys come back as str, not bytes.
        if self.redis_password:
            return Redis.from_url(self.redis_url, decode_responses=True, password=self.redis_password)
        return Redis.from_url(self.redis_url, decode_responses=True)
