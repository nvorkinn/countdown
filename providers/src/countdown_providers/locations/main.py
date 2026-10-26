"""The locations provider: where each postcode in `v1:sub:postcode` is, written to `v1:location:<place>`.

And where each UK postcode or postcode district that a `v1:sub:weather` key is the slug of (`sw1a-1aa`, `se17`) is,
for the weather provider (WeatherPostcodesProvider, a second runner in the same process).

The one process that looks postcodes up: a UK postcode with postcodes.io (countdown_core.notices.location's
lookup_postcode), a Norwegian one (four digits, as the broker writes them) with Kartverket's address search
(countdown_core.notices.norway_feed's lookup_norwegian_postcode). Each is written as a StoredLocation (keys.location),
which the notice board reads for what it shows about its place (the bank holidays' division, MetAlerts' area), so the
render pool asks neither, and so do the Met Office, floods and TfL roads providers, for where to fetch their notices
(reader.py), so each postcode is looked up once in all. A postcode neither knows is written too, as not found, so a
board can say "Unknown postcode" rather than wait for a location that won't come.

A postcode's place doesn't move, so each is looked up once and that answer is written again every cycle, which keeps
its key for ttl_s (days) after the postcode leaves the set or the provider stops. It's asked about again once a day
(LOOKUP_EVERY), for postcodes.io's corrections and its newly issued postcodes. A lookup that fails leaves the last
answer in place, and a postcode never yet looked up is left out, so its board waits and it's tried again next cycle. A
postcode found once isn't written over by a later "not found": its screens keep their notices if postcodes.io drops
it (a terminated postcode) or Kartverket answers with nothing for a while.

Nothing logged names a postcode or anything worked out from one (its country, district or coordinates, or a URL with
it in): a postcode says where a screen, and someone's home, is, and the logs go to the central log store. A failure is
logged by the error's type (log_format.error_reason), and urllib3's retry warnings, which quote the URL, are kept out
of every process's logs (log_format.configure_logging).

Once it has written a place that's new (a postcode new to the set, or to this process since it started) or has moved
(postcodes.io's corrections), the runner publishes on `v1:location:changed` (keys.LOCATION_CHANGED; `announces` and
`is_news`, see provider.Provider), which wakes the providers that read the places (their `wakes_on`). Without it, a
new postcode's notices would wait a whole interval: the config save's nudge runs every provider at once, so they read
before its lookup is done. A postcode that isn't found, or whose place is unchanged, isn't announced: nothing about it
would change.

The lookups are synchronous (requests), so each batch runs in a worker thread (asyncio.to_thread), one at a time
however many runners ask (the requests session isn't shared across threads).
"""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from countdown_core.core.http_session import retrying_session
from countdown_core.core.log_format import configure_logging, error_reason
from countdown_core.notices.location import FULL_POSTCODE, OUTCODE, lookup_outcode, lookup_postcode
from countdown_core.notices.norway_feed import lookup_norwegian_postcode
from countdown_core.store import keys
from countdown_core.store.location import StoredLocation, weather_postcode
from countdown_providers.config import Settings
from countdown_providers.runner import serve_all

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

    from countdown_core.tfl.models import Postcode

logger = logging.getLogger(__name__)

# As the broker writes them (its pages.NORWEGIAN_POSTCODE_RE): a UK postcode always has letters.
NORWEGIAN_POSTCODE = re.compile(r"[0-9]{4}")
# How long a lookup's answer is used before the postcode is asked about again.
LOOKUP_EVERY = timedelta(days=1)


class LocationsProvider:
    """Where the subscribed postcodes are, from postcodes.io and Kartverket (both keyless)."""

    name = keys.POSTCODE
    # A week: a provider that's down for days doesn't unplace any board, and a postcode no screen has any more is gone
    # within a week of the last screen dropping it.
    ttl_s = 7 * 24 * 60 * 60
    # Only a postcode new to the set, due again (LOOKUP_EVERY) or whose last lookup failed is asked about; a new one
    # is asked about at once anyway, as the broker's nudge runs a cycle when a config is saved.
    interval_s = 5 * 60
    # Published on (by the runner) after writing a place that's new or has moved (is_news).
    announces = keys.LOCATION_CHANGED

    def __init__(self, session: requests.Session, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask postcodes.io and Kartverket through `session`; `now` stamps fetched_at (a parameter for the tests)."""
        self.session = session
        self.now = now
        # Postcode -> what was last looked up for it. Only grows (a hundred bytes or so each), like the other place
        # providers' caches.
        self.locations: dict[str, StoredLocation] = {}
        # Postcode -> the place last announced for it (is_news). Only grows, as `locations` does.
        self.announced: dict[str, Postcode] = {}
        # One batch's lookups at a time: the weather's postcodes (WeatherPostcodesProvider) are looked up here too.
        self._lookups = asyncio.Lock()

    def output_key(self, item_id: str) -> str:
        """Write to `v1:location:<place>`."""
        return keys.location(item_id)

    def is_news(self, item_id: str, written: StoredLocation) -> bool:
        """Say whether the runner should announce `written`, which it has just written: a place new here, or moved.

        Remembers what it said, so each place is announced once.
        """
        if written.location is None or self.announced.get(item_id) == written.location:
            return False
        self.announced[item_id] = written.location
        return True

    async def fetch(self, ids: list[str]) -> dict[str, StoredLocation]:
        """Return where each postcode is, or that it doesn't exist; leaves out one that couldn't be looked up yet."""
        # One thread for the whole batch, one request after another: the requests session isn't shared across threads.
        async with self._lookups:
            return await asyncio.to_thread(self._fetch_blocking, ids)

    def _fetch_blocking(self, ids: list[str]) -> dict[str, StoredLocation]:
        results: dict[str, StoredLocation] = {}
        for postcode in ids:
            location = self._location(postcode)
            if location is not None:
                results[postcode] = location
        return results

    def _location(self, postcode: str) -> StoredLocation | None:
        """Return the postcode's location, looked up unless it was less than LOOKUP_EVERY ago; None if never placed."""
        now = self.now()
        last = self.locations.get(postcode)
        if last is not None and now - last.fetched_at < LOOKUP_EVERY:
            return last
        try:
            place = self._look_up(postcode)
        except Exception as e:  # noqa: BLE001 -- one postcode mustn't lose the batch; it's tried again next cycle
            logger.warning("Couldn't look up a postcode: %s", error_reason(e))
            return last
        if place is None and last is not None and last.found:
            logger.warning("A postcode that was found before isn't found now; keeping where it was")
            location = last.model_copy(update={"fetched_at": now})
        else:
            if place is None:
                logger.warning("A subscribed postcode isn't one postcodes.io or Kartverket knows; stored as not found")
            location = StoredLocation(fetched_at=now, postcode=postcode, found=place is not None, location=place)
        self.locations[postcode] = location
        return location

    def _look_up(self, postcode: str) -> Postcode | None:
        """Ask the postcode's country's service where it is; None if it doesn't know it. Raises if it can't be asked."""
        if NORWEGIAN_POSTCODE.fullmatch(postcode):
            return lookup_norwegian_postcode(self.session, postcode)
        if FULL_POSTCODE.fullmatch(postcode):
            return lookup_postcode(self.session, postcode)
        if OUTCODE.fullmatch(postcode):  # a district: only a weather key's (the broker's postcodes are whole ones)
            return lookup_outcode(self.session, postcode)
        return None  # no shape postcodes.io or Kartverket know, so nobody could place it


class WeatherPostcodesProvider:
    """Where the UK postcodes and postcode districts that weather location keys are slugs of are, for provider-weather.

    A postcode typed as a screen's weather location (not picked as a place) is subscribed to as its slug (`sw1a-1aa`,
    `se17`: countdown_core.store.location.weather_postcode). Each is looked up by
    `postcodes`, the LocationsProvider of this process, so with its cache, and written under the postcode's own key
    (`v1:location:SW1A1AA`, `v1:location:SE17`), as for a notice board's. Any other weather key (a picked place's, or
    other typed text's) is the weather provider's to look up with Open-Meteo, and is left out.
    """

    name = keys.WEATHER
    ttl_s = LocationsProvider.ttl_s
    interval_s = LocationsProvider.interval_s
    # Published on (by the runner) after writing a place that's new or has moved, as LocationsProvider's is, so the
    # weather provider (its wakes_on) reads a newly placed postcode at once.
    announces = keys.LOCATION_CHANGED

    def __init__(self, postcodes: LocationsProvider) -> None:
        """Look the postcodes up with `postcodes`."""
        self.postcodes = postcodes

    def output_key(self, item_id: str) -> str:
        """Write to `v1:location:<place>`, the key of the postcode `item_id` is the slug of."""
        postcode = weather_postcode(item_id)
        if postcode is None:  # fetch() returns only those that are one
            msg = "not the slug of a postcode"
            raise ValueError(msg)
        return keys.location(postcode)

    def is_news(self, item_id: str, written: StoredLocation) -> bool:
        """Announce as LocationsProvider does, by the postcode the key is the slug of (one place, announced once)."""
        return self.postcodes.is_news(weather_postcode(item_id) or item_id, written)

    async def fetch(self, ids: list[str]) -> dict[str, StoredLocation]:
        """Return where the postcode each key is the slug of is, by key; leaves out other keys, as LocationsProvider."""
        wanted = {key: postcode for key in ids if (postcode := weather_postcode(key)) is not None}
        if not wanted:
            return {}
        located = await self.postcodes.fetch(sorted(set(wanted.values())))
        return {key: located[postcode] for key, postcode in wanted.items() if postcode in located}


def main() -> None:
    """Run the `countdown-provider-locations` console script. postcodes.io and Kartverket are keyless."""
    configure_logging()
    # countdown_core's retrying session: retries connection errors and 502/503/504 with backoff.
    session = retrying_session()
    postcodes = LocationsProvider(session)
    asyncio.run(serve_all([postcodes, WeatherPostcodesProvider(postcodes)], Settings.from_env()))


if __name__ == "__main__":
    main()
