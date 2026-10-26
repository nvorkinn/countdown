"""Where the subscribed postcodes are, as provider-locations stored them, for the providers of notices about a place.

provider-locations (main.py) is the one process that looks postcodes up. The Met Office, floods and TfL roads providers
read its `v1:location:<place>` keys (keys.location) instead of asking postcodes.io themselves, so each postcode is
looked up once in all, however many providers need to know where it is. A batch costs one MGET, on the provider's own
Redis client: the one its runner reads the subscriptions with (runner.serve_all), so a process keeps one connection
pool.

A postcode the store can't place is left out of that batch, and only counted in the log; it's read again next cycle:

- one with no key yet: new to `v1:sub:postcode`, and provider-locations hasn't placed it yet (a config save's nudge runs
  every provider at once; once it has, it publishes on `v1:location:changed`, which runs another cycle of the
  providers that set it in their `wakes_on`) or couldn't (it says why in its own log);
- one whose key isn't a StoredLocation (a provider-locations on another version);
- one stored as not found: a postcode that doesn't exist gets no notices.

A Redis that can't be read leaves the whole batch out. Either way the notices already stored for those postcodes stay
until their TTL, and the other postcodes carry on.

Nothing logged names a postcode, or anything about where it is (the logs go to the central log store): only how many,
and an error's type (log_format.error_reason; a failed command's text can quote its keys).
"""

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError
from redis.exceptions import RedisError

from countdown_shared.core.log_format import error_reason
from countdown_shared.store import keys
from countdown_shared.store.location import StoredLocation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redis.asyncio import Redis

    from countdown_shared.tfl.models import Postcode

logger = logging.getLogger(__name__)


class LocationReader:
    """Reads where postcodes are from `redis` (made with decode_responses=True), as provider-locations wrote them."""

    def __init__(self, redis: Redis) -> None:
        """Read through `redis`: the process's own client, which its runner uses too."""
        self.redis = redis

    async def places(self, postcodes: Sequence[str], *, notices: str) -> dict[str, Postcode]:
        """Return where each of `postcodes` is, in their order, leaving out those the store can't place (see above).

        `notices` says, in the log, what those go without this cycle ("Met Office warnings"). Never raises.
        """
        if not postcodes:
            return {}  # MGET needs a key
        try:
            values = await self.redis.mget([keys.location(postcode) for postcode in postcodes])
        except RedisError as e:
            logger.warning(
                "Couldn't read from the store where the batch's postcodes are (%d), so no %s for them this cycle: %s",
                len(postcodes),
                notices,
                error_reason(e),
            )
            return {}
        places: dict[str, Postcode] = {}
        missing = unreadable = unknown = 0
        for postcode, value in zip(postcodes, values, strict=True):
            if value is None:
                missing += 1
                continue
            try:
                stored = StoredLocation.model_validate_json(value)
            except ValidationError:  # not logged: its text quotes the value, which says where the postcode is
                unreadable += 1
                continue
            if stored.location is None:
                unknown += 1
            else:
                places[postcode] = stored.location
        if missing:
            logger.info(
                "No location in the store yet for %d of %d postcodes, so no %s for them this cycle "
                "(provider-locations writes them)",
                missing,
                len(postcodes),
                notices,
            )
        if unreadable:
            logger.warning(
                "Couldn't read the location in the store of %d of %d postcodes (not a StoredLocation), so no %s for "
                "them this cycle",
                unreadable,
                len(postcodes),
                notices,
            )
        if unknown:
            # provider-locations warns about each once a day; this would every cycle.
            logger.debug("%d of %d postcodes don't exist, so no %s for them", unknown, len(postcodes), notices)
        return places
