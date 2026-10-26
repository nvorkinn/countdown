"""Where a postcode is, in the store: the value of `v1:location:<place>` (keys.location).

Written by provider-locations for each postcode in `v1:sub:postcode`, from postcodes.io (a UK postcode) or Kartverket's
address search (a Norwegian one, four digits), and read by the notice board for which sources it has about its place
and what they need from it (the bank holidays' division, the Met Office region, MetAlerts' place). Their answers are
kept in the fields the board uses (countdown_core.tfl.models.Postcode, which both lookups give), so one shape serves
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
"""

from typing import Self

from pydantic import model_validator

from countdown_core.notices.location import FULL_POSTCODE, OUTCODE
from countdown_core.store.models import Stored
from countdown_core.tfl.models import Postcode  # noqa: TC001 -- pydantic needs it at runtime


class StoredLocation(Stored):
    """Where a postcode is, as looked up at `fetched_at`; or that it isn't one postcodes.io or Kartverket know."""

    # As `v1:sub:postcode` has it ("SE17 3LL", "0150"); for a weather key's, as weather_postcode() gives it ("SE17").
    postcode: str
    # False for a postcode that doesn't exist (a typo, or one that's been terminated): it has no location.
    found: bool
    location: Postcode | None = None

    @model_validator(mode="after")
    def _found_has_a_location(self) -> Self:
        if self.found != (self.location is not None):
            msg = "a found postcode has a location, and one that wasn't found has none"
            raise ValueError(msg)
        return self


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
