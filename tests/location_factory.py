"""Where postcodes are, in a fake store, as provider-locations writes them, for the providers that read it there."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import fakeredis

from countdown_providers.locations.reader import LocationReader
from countdown_shared.store import keys
from countdown_shared.store.location import StoredLocation

if TYPE_CHECKING:
    from countdown_shared.tfl.models import Postcode

LOOKED_UP = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)


class FakeLocations:
    """A fake Redis holding where postcodes are (`v1:location:<place>`), and a LocationReader reading it.

    `redis` is the providers' (async) side, which a test's runner uses too, as a provider's main() shares its one
    client; `store` is a sync side of the same Redis, for the test to change what's there.
    """

    def __init__(
        self, *places: Postcode, not_found: tuple[str, ...] = (), server: fakeredis.FakeServer | None = None
    ) -> None:
        """Store `places`, each under its own postcode, and `not_found` as postcodes that don't exist.

        In `server` if given: a test whose renderer reads the same store.
        """
        server = server or fakeredis.FakeServer()
        self.store = fakeredis.FakeRedis(server=server, decode_responses=True)
        self.redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        self.reader = LocationReader(self.redis)
        self.put(*places, not_found=not_found)

    def put(self, *places: Postcode, not_found: tuple[str, ...] = ()) -> None:
        """Store `places` and `not_found` as provider-locations would, over whatever was there."""
        for place in places:
            self._set(StoredLocation(fetched_at=LOOKED_UP, postcode=place.postcode, found=True, location=place))
        for postcode in not_found:
            self._set(StoredLocation(fetched_at=LOOKED_UP, postcode=postcode, found=False))

    def remove(self, *postcodes: str) -> None:
        """Take the postcodes' locations out of the store (their keys lapsed, say)."""
        self.store.delete(*(keys.location(postcode) for postcode in postcodes))

    def _set(self, stored: StoredLocation) -> None:
        self.store.set(keys.location(stored.postcode), stored.model_dump_json())
