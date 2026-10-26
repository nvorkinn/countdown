"""Where the providers fetch a Norwegian screen's data from: Kartverket for its place, MET Norway for its warnings.

provider-locations asks Kartverket (lookup_norwegian_postcode); the MetAlerts provider asks MET (METALERTS_URL). The
notice board reads both answers from the store (notices/norway.py), and asks neither.
"""

from typing import TYPE_CHECKING

from countdown_core.core.http_session import DEFAULT_TIMEOUT
from countdown_core.tfl.models import Postcode

if TYPE_CHECKING:
    import requests

# Kartverket's address search: free, no key. A postcode's addresses all carry its place name and municipality.
ADDRESSES_URL = "https://ws.geonorge.no/adresser/v1/sok"
METALERTS_URL = "https://api.met.no/weatherapi/metalerts/2.0/current.json"
# api.met.no refuses requests that don't say who's asking (https://api.met.no/doc/TermsOfService).
MET_HEADERS = {"User-Agent": "countdown https://github.com/nvorkinn/countdown"}


def lookup_norwegian_postcode(session: requests.Session, postcode: str) -> Postcode | None:
    """Look up where a Norwegian postcode is, as a Postcode with its municipality as the district.

    provider-locations' lookup, which it stores for the board (keys.location). None if Kartverket has no address with
    it; raises if Kartverket can't be reached, so the caller can try again later.
    """
    response = session.get(
        ADDRESSES_URL,
        params={"postnummer": postcode.strip(), "treffPerSide": 1, "utkoordsys": 4258},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    addresses = response.json().get("adresser") or []
    if not addresses:
        return None
    address = addresses[0]
    point = address.get("representasjonspunkt") or {}
    if point.get("lat") is None or point.get("lon") is None:
        return None
    return Postcode(
        postcode=address.get("postnummer") or postcode,
        latitude=point["lat"],
        longitude=point["lon"],
        country="Norway",
        admin_district=(address.get("kommunenavn") or "").title() or None,
    )
