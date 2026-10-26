"""Placing a UK postcode, or a postcode district, with postcodes.io (provider-locations is the one caller)."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

from countdown_shared.core.http_session import DEFAULT_TIMEOUT
from countdown_shared.tfl.models import OutcodeResponse, Postcode, PostcodeResponse

if TYPE_CHECKING:
    import requests

POSTCODES_URL = "https://api.postcodes.io/postcodes"
OUTCODES_URL = "https://api.postcodes.io/outcodes"

_OUTCODE = r"[A-Z]{1,2}\d[A-Z\d]?"
FULL_POSTCODE = re.compile(rf"{_OUTCODE}\s*\d[A-Z]{{2}}", re.IGNORECASE)
OUTCODE = re.compile(_OUTCODE, re.IGNORECASE)


def lookup_postcode(session: requests.Session, postcode: str) -> Postcode | None:
    """Look up where a UK postcode is: coordinates, country, English region and local authority.

    None if postcodes.io doesn't know it (a typo, or a terminated postcode); raises if postcodes.io can't be reached,
    so the caller can try again later.
    """
    response = session.get(f"{POSTCODES_URL}/{postcode.strip()}", timeout=DEFAULT_TIMEOUT)
    if response.status_code == HTTPStatus.NOT_FOUND:
        return None
    response.raise_for_status()
    return PostcodeResponse.model_validate_json(response.content).result


def lookup_outcode(session: requests.Session, outcode: str) -> Postcode | None:
    """Look up where a UK postcode district ("SE17") is: its centre, as a Postcode with the district for its postcode.

    Its country is every country postcodes.io says the district is in, joined ("England", or "England, Wales" for one
    across the border), and it has no region or district of its own. None if postcodes.io doesn't know it; raises if
    postcodes.io can't be reached, so the caller can try again later.
    """
    outcode = outcode.strip()
    response = session.get(f"{OUTCODES_URL}/{outcode}", timeout=DEFAULT_TIMEOUT)
    if response.status_code == HTTPStatus.NOT_FOUND:
        return None
    response.raise_for_status()
    result = OutcodeResponse.model_validate_json(response.content).result
    return Postcode(
        postcode=outcode.upper(),
        latitude=result.latitude,
        longitude=result.longitude,
        country=", ".join(result.country),
    )
