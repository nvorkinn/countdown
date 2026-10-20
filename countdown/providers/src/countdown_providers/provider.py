"""What a provider is: the one part of each API's process that differs. The loop around it is runner.ProviderRunner."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel


class Provider(Protocol):
    """Fetches one API's data for a list of ids. The runner decides which ids, when, and writes the results."""

    # The subscription source this provider serves: the runner reads the ids from `v1:sub:<name>`.
    name: str
    # How long a written value lives. Longer than interval_s, so one failed cycle doesn't hide a panel, but short
    # enough that a provider that's down shows up as missing data rather than as stale data.
    ttl_s: int
    # How often a cycle runs, in seconds. Cycles are aligned to the epoch (every provider with the same interval
    # ticks together), and a subscription change runs one early.
    interval_s: float

    def output_key(self, item_id: str) -> str:
        """Return the Redis key to write `item_id`'s data to, from countdown_core.store.keys."""
        ...

    async def fetch(self, ids: list[str]) -> Mapping[str, BaseModel]:
        """Fetch the data for `ids`, as a model per id that could be fetched.

        One bad id (unknown, or its API call failed) is left out of the result and logged; it mustn't raise and lose
        the others. Raising is for failing the whole batch, e.g. the API being down; the runner logs it and carries on.
        """
        ...

    # Optional, and not part of the protocol: a provider that fetches something once for a whole batch (TfL's line
    # status, for every stop's lines at once) can set an int `batch_size`, how many ids to pass to fetch() at a time,
    # in place of the runner's DEFAULT_BATCH_SIZE. A large one makes every id in the shard one batch.
    #
    # Also optional: a provider whose data can turn bursty (a release running) defines
    #
    #     def pace(self, item_id: str, fetched: BaseModel) -> int | None
    #
    # returning the seconds it wants the screen to refresh at for what it just fetched, or None if it needs no more
    # than usual. The runner writes that to `v1:pace:<name>:<item id>` with a TTL of PACE_TTL_S (and deletes the key on
    # None), so the pace lapses by itself if the provider stops. The broker times the screen's polls by the fastest
    # of the screen's own (auth-broker's services/pace.py).
    #
    # Also optional: a provider that fetches the same thing for every screen, whatever they subscribe to (gov.uk's one
    # bank holidays file for all of the UK), defines `fixed_ids`, a tuple of the ids it always fetches. The runner
    # fetches those every cycle instead of reading `v1:sub:<name>` (`name` then only names it in the logs), and doesn't
    # listen for subscription changes, which can't change what it fetches.
    #
    # Also optional, for a provider whose keys other providers read (provider-locations' `v1:location:`): it sets
    # `announces`, a pub/sub channel, and defines
    #
    #     def is_news(self, item_id: str, written: BaseModel) -> bool
    #
    # which the runner asks about each value once it's written; after writing a batch with any news in it, the runner
    # publishes (empty) on `announces`. A provider that reads those keys sets `wakes_on`, a tuple of such channels: a
    # message on one runs a cycle early, as one on `v1:sub:changed` does. So a new postcode's notices needn't wait a
    # whole interval for its place to be written.
