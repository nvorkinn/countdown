"""The Met Office provider: the weather warnings for every UK postcode in `v1:sub:postcode`.

Where each postcode is comes from the store, as provider-locations placed it (`v1:location:<place>`, read each cycle
by locations.reader.LocationReader; one it hasn't placed, or that doesn't exist, is left out until it has), and gives
the Met Office warning region it's in (countdown_core.notices.met_office.region_for). The Met Office publishes a
warnings RSS feed per region, so each region's feed is fetched once a cycle however many postcodes are in it, and
written under each of them, `v1:notices:met-office:<place>`, as a StoredMetOfficeWarnings: the feed's items, which the
render pool makes its notices from when it draws. A Norwegian postcode (four digits) is MET Norway's to warn about, not
the Met Office's, so it's left alone.

Where the screens are stays out of the logs: nothing logged here names a postcode, or the region one is in.

Its HTTP calls are synchronous (requests), so each batch runs in a worker thread (asyncio.to_thread), like the weather
provider's.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from countdown_core.core.http_session import DEFAULT_TIMEOUT, retrying_session
from countdown_core.core.log_format import configure_logging, error_reason
from countdown_core.notices.location import FULL_POSTCODE
from countdown_core.notices.met_office import region_for
from countdown_core.store import keys
from countdown_core.store.met_office import FeedItem, StoredMetOfficeWarnings
from countdown_providers.config import Settings
from countdown_providers.locations.reader import LocationReader
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

    from countdown_core.tfl.models import Postcode

logger = logging.getLogger(__name__)

FEED_URL = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/{region}"


def feed_items(feed: bytes) -> list[FeedItem]:
    """Read the feed's <item>s, in its order, each as the text of its elements."""
    return [
        FeedItem(
            title=item.findtext("title") or "",
            description=item.findtext("description") or "",
            link=item.findtext("link") or "",
            pub_date=item.findtext("pubDate") or "",
        )
        for item in ET.fromstring(feed).iter("item")  # noqa: S314 -- the Met Office's own feed, over https
    ]


@dataclass(frozen=True)
class Feed:
    """A region's feed as it was at `fetched_at`: its items, or None if it couldn't be fetched or read then."""

    fetched_at: datetime
    items: list[FeedItem] | None


class MetOfficeProvider:
    """Weather warnings for the subscribed UK postcodes, from the Met Office's regional feeds (keyless)."""

    name = keys.POSTCODE
    # Six cycles: a provider that's down shows as missing warnings within half an hour, not as old ones.
    ttl_s = 30 * 60
    # Warnings are issued hours ahead and change rarely.
    interval_s = 5 * 60
    # A cycle as soon as provider-locations places a new postcode (or moves one), not at the next tick.
    wakes_on = (keys.LOCATION_CHANGED,)
    # The runner hands fetch() the postcodes a batch at a time, one batch after another, so a feed fetched less than
    # this long ago was fetched this cycle, and is used again rather than fetched again; the next tick's cycle is a
    # whole interval later, and one woken sooner (wakes_on) uses the feeds the last one fetched, asking only for new
    # regions.
    feed_reuse = timedelta(seconds=interval_s / 2)

    def __init__(
        self,
        session: requests.Session,
        locations: LocationReader,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Ask the Met Office through `session`, and the store where each postcode is through `locations`.

        `now` stamps fetched_at (a parameter for tests).
        """
        self.session = session
        self.locations = locations
        self.now = now
        # Postcode -> where it was last read to be, and the Met Office region that's in: worked out again only if
        # provider-locations moves it, so region_for's warning about a place in no region it knows is logged once,
        # not every cycle. Only grows (a hundred bytes or so each), like provider-locations' own.
        self.regions: dict[str, tuple[Postcode, str]] = {}
        # Region -> its feed as last fetched (or tried); at most one per region.
        self.feeds: dict[str, Feed] = {}

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:met-office:<place>`."""
        return keys.met_office(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredMetOfficeWarnings]:
        """Fetch the warnings for each UK postcode, leaving out (and logging) any that can't be placed or fetched."""
        # UK postcodes only: a Norwegian one ("0150") gets MET Norway's warnings instead.
        postcodes = [postcode for postcode in ids if FULL_POSTCODE.fullmatch(postcode)]
        places = await self.locations.places(postcodes, notices="Met Office warnings")
        if not places:
            return {}
        # One thread for the whole batch, one request after another: the requests session isn't shared across threads.
        return await asyncio.to_thread(self._fetch_blocking, places)

    def _fetch_blocking(self, places: dict[str, Postcode]) -> dict[str, StoredMetOfficeWarnings]:
        results: dict[str, StoredMetOfficeWarnings] = {}
        for postcode, location in places.items():
            region = self._region(postcode, location)
            feed = self._feed(region)
            if feed.items is not None:
                results[postcode] = StoredMetOfficeWarnings(
                    fetched_at=feed.fetched_at, postcode=postcode, region=region, items=feed.items
                )
        return results

    def _region(self, postcode: str, location: Postcode) -> str:
        """Return the Met Office region `location` is in, worked out the first time and again if it moves."""
        known = self.regions.get(postcode)
        if known is None or known[0] != location:
            known = (location, region_for(location))
            self.regions[postcode] = known
        return known[1]

    def _feed(self, region: str) -> Feed:
        """Return the region's feed, fetching it unless that was already done (or tried) this cycle."""
        now = self.now()
        feed = self.feeds.get(region)
        if feed is None or now - feed.fetched_at >= self.feed_reuse:
            feed = Feed(now, self._fetch_feed(region))
            self.feeds[region] = feed
        return feed

    def _fetch_feed(self, region: str) -> list[FeedItem] | None:
        """Fetch and read the region's feed; None (and logged) if it can't be, so its postcodes are left out."""
        try:
            response = self.session.get(FEED_URL.format(region=region), timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            return feed_items(response.content)
        except Exception as e:  # noqa: BLE001 -- the other regions carry on; this one's keys expire if it keeps failing
            # Neither the region nor the error's text, which quotes the feed's URL, region and all: a region is worked
            # out from a screen's postcode, and one with few screens in it says where they are.
            logger.warning("Couldn't fetch a region's Met Office warnings: %s", error_reason(e))
            return None


def main() -> None:
    """Run the `countdown-provider-met-office` console script. The Met Office's feeds are keyless."""
    configure_logging()
    settings = Settings.from_env()
    # One client for the runner and the locations it reads.
    redis = settings.redis()
    # countdown_core's retrying session: retries connection errors and 502/503/504 with backoff.
    session = retrying_session()
    asyncio.run(serve(MetOfficeProvider(session, LocationReader(redis)), settings, redis=redis))


if __name__ == "__main__":
    main()
