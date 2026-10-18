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
"""

from typing import Self

from pydantic import model_validator

from countdown_core.store.models import Stored
from countdown_core.tfl.models import Postcode  # noqa: TC001 -- pydantic needs it at runtime


class StoredLocation(Stored):
    """Where a postcode is, as looked up at `fetched_at`; or that it isn't one postcodes.io or Kartverket know."""

    # As `v1:sub:postcode` has it ("SE17 3LL", "0150").
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
