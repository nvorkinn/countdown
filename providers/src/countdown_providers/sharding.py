"""Splitting a provider's ids between several copies of it, so each id is fetched by exactly one."""

import hashlib
from dataclasses import dataclass


def shard_of(item_id: str, count: int) -> int:
    """Return which of `count` shards owns `item_id`: the same in every process, on every run.

    A hash of the id, not Python's hash(), which is salted differently in each process (PYTHONHASHSEED), so two
    copies would disagree about who owns what.
    """
    digest = hashlib.sha256(item_id.encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


@dataclass(frozen=True)
class Shard:
    """A process's share of the ids: shard `index` of `count` (SHARD_INDEX and SHARD_COUNT). 0 of 1 is all of them."""

    index: int = 0
    count: int = 1

    def __post_init__(self) -> None:
        """Refuse a shard that would own nothing, or a share of nothing."""
        if self.count < 1:
            msg = f"SHARD_COUNT must be at least 1, not {self.count}"
            raise ValueError(msg)
        if not 0 <= self.index < self.count:
            msg = f"SHARD_INDEX must be from 0 to {self.count - 1} (SHARD_COUNT - 1), not {self.index}"
            raise ValueError(msg)

    def owns(self, item_id: str) -> bool:
        """Whether this process fetches `item_id`."""
        return shard_of(item_id, self.count) == self.index
