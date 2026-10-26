"""The Met Office's weather warnings in the store: the value of `v1:notices:met-office:<place>` (keys.met_office).

Written by provider-met-office for each UK postcode in `v1:sub:postcode`: the warnings RSS of the Met Office region the
postcode is in (countdown_core.notices.met_office_feed.region_for), its <item>s as JSON. A region's feed is fetched
once however many postcodes are in it, and written under each of them, so a renderer finds its warnings by its
postcode alone. The notices themselves (their text in the screen's language, their severity, when each ends) are worked
out by the renderer when it draws (countdown_core.notices.met_office.warning_notices).

    {"fetched_at": "2026-09-25T11:55:00Z", "postcode": "SE17 3LL", "region": "se",
     "items": [{"title": "Amber warning of wind affecting London & South East England",
                "description": "Amber warning of wind affecting ...: Lambeth, Southwark valid from 0600 Fri 25 Sep ...",
                "link": "https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings#?...",
                "pub_date": "Thu, 24 Sep 2026 10:30:00 GMT"}]}

tests/fixtures/met_office_warnings.json is a stored value, from the feed in tests/fixtures/met_office_warnings.xml.
"""

from pydantic import BaseModel

from countdown_core.store.models import Stored


class FeedItem(BaseModel):
    """One <item> of the feed: the text of its elements as the feed has them (entities decoded), "" for one it lacks."""

    title: str = ""
    # Says where and until when: "... affecting London & South East England: Lambeth, Southwark valid from 0600 Fri
    # 25 Sep to 2100 Fri 25 Sep".
    description: str = ""
    # The warning's page on the Met Office's site.
    link: str = ""
    # Its <pubDate>, as the feed writes it (RFC 822): "Thu, 24 Sep 2026 10:30:00 GMT".
    pub_date: str = ""


class StoredMetOfficeWarnings(Stored):
    """The warnings for a postcode's place: its Met Office region's feed, as it was at `fetched_at`."""

    # The postcode it's for, as `v1:sub:postcode` has it ("SE17 3LL").
    postcode: str
    # The Met Office warning region whose feed it is ("se"), or "UK" for the whole of the UK's.
    region: str
    # The feed's <item>s, in its order; empty when there are no warnings.
    items: list[FeedItem]
