"""Where a postcode is, in the store: the value of `v1:location:<place>` (keys.location).

Written by provider-locations for each postcode in `v1:sub:postcode`, from postcodes.io (a UK postcode) or Kartverket's
address search (a Norwegian one, four digits), and read by the notice board for which sources it has about its place
and what they need from it (the bank holidays' division, the Met Office region, MetAlerts' place). Their answers are
kept in the fields the board uses (countdown_shared.tfl.models.Postcode, which both lookups give), so one shape serves
both countries:

    {"fetched_at": "2026-10-18T12:00:00Z", "postcode": "SE17 3LL", "found": true,
     "location": {"postcode": "SE17 3LL", "latitude": 51.48873, "longitude": -0.09766, "country": "England",
                  "region": "London", "admin_district": "Southwark"}}

A postcode that neither knows is written too, as `{"found": false, "location": null}`, so a board can say "Unknown
postcode" rather than wait for a location that will never come. tests/fixtures/locations.json has examples.

provider-locations places the UK postcodes, and postcode districts, that weather location keys stand for the same way
(weather_postcode): a screen whose weather is a typed postcode is subscribed as its slug (`sw1a-1aa`), and the
weather provider reads where it is from here rather than ask postcodes.io.
A district's key (`v1:location:SE17`) holds its centre, with the district for its postcode.

The value is the store contract's StoredLocation (countdown-api's store.yaml: `location`), with its Postcode, as
countdown-server reads it too (for MetAlerts' place in a touchscreen's notices), re-exported here. The contract's
location.json (countdown_api.fixtures) is an example. A found postcode has a location and one that wasn't found has
none: provider-locations writes them so.
"""

from countdown_api.models import StoredLocation
from countdown_shared.notices.location import FULL_POSTCODE, OUTCODE

__all__ = ["StoredLocation", "weather_postcode"]


def weather_postcode(key: str) -> str | None:
    """Return the UK postcode or postcode district a weather location key is the slug of, else None.

    In the form `v1:sub:postcode` has a postcode in ("SW1A 1AA" for `sw1a-1aa` or `sw1a1aa`), or a district's upper
    case ("SE17" for `se17`), so keys.location() gives its key. None for any other key: a picked place's
    (`london-gb-2643743`), or the slug of other typed text (`london`, `oslo-0150`, `0150`), which the weather provider
    asks Open-Meteo about.
    """
    text = key.replace("-", " ").strip()
    if FULL_POSTCODE.fullmatch(text):
        compact = text.replace(" ", "").upper()
        return f"{compact[:-3]} {compact[-3:]}"
    if OUTCODE.fullmatch(text):
        return text.upper()
    return None
