import re
from typing import TYPE_CHECKING

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.tfl.models import OutcodeResponse, Postcode, PostcodeResponse

if TYPE_CHECKING:
    import requests

POSTCODES_URL = "https://api.postcodes.io/postcodes"
OUTCODES_URL = "https://api.postcodes.io/outcodes"

_OUTCODE = r"[A-Z]{1,2}\d[A-Z\d]?"
FULL_POSTCODE = re.compile(rf"{_OUTCODE}\s*\d[A-Z]{{2}}", re.IGNORECASE)
OUTCODE = re.compile(_OUTCODE, re.IGNORECASE)


def lookup_postcode(session: requests.Session, postcode: str) -> Postcode | None:
    """Where a UK postcode is: coordinates, country, English region and local authority.
    None if postcodes.io doesn't know it (a typo, or a terminated postcode); raises if
    postcodes.io can't be reached, so the caller can try again later.
    """
    response = session.get(f"{POSTCODES_URL}/{postcode.strip()}", timeout=DEFAULT_TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return PostcodeResponse.model_validate_json(response.content).result


def is_postcode(text: str) -> bool:
    """Shaped like a UK postcode ("SE17 2PX") or postcode district ("SE17")."""
    text = text.strip()
    return bool(FULL_POSTCODE.fullmatch(text) or OUTCODE.fullmatch(text))


def postcode_coordinates(session: requests.Session, text: str) -> tuple[float, float] | None:
    """Coordinates for a UK postcode or postcode district. None if postcodes.io doesn't
    know it (or the text is neither); raises if postcodes.io can't be reached.
    """
    text = text.strip()
    if FULL_POSTCODE.fullmatch(text):
        postcode = lookup_postcode(session, text)
        return (postcode.latitude, postcode.longitude) if postcode else None
    if OUTCODE.fullmatch(text):
        response = session.get(f"{OUTCODES_URL}/{text}", timeout=DEFAULT_TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        outcode = OutcodeResponse.model_validate_json(response.content).result
        return (outcode.latitude, outcode.longitude)
    return None
