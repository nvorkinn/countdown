"""Which of the Met Office's warnings feeds a UK postcode is in, for provider-met-office: its region, by where it is.

The notices are made from the feed's items by the board as it draws (notices/met_office.py).
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from countdown_shared.tfl.models import Postcode

logger = logging.getLogger(__name__)

# Met Office warning regions, keyed by postcodes.io's English region or, outside
# England, its country. Scotland is split further, by council area (below).
REGION_BY_AREA = {
    "London": "se",
    "South East": "se",
    "South West": "sw",
    "East of England": "ee",
    "East Midlands": "em",
    "West Midlands": "wm",
    "Yorkshire and The Humber": "yh",
    "North West": "nw",
    "North East": "ne",
    "Wales": "wl",
    "Northern Ireland": "ni",
}
SCOTTISH_REGION_BY_COUNCIL = {
    "Orkney Islands": "os",
    "Shetland Islands": "os",
    "Highland": "he",
    "Na h-Eileanan Siar": "he",
    "Aberdeen City": "gr",
    "Aberdeenshire": "gr",
    "Moray": "gr",
    "Angus": "ta",
    "Dundee City": "ta",
    "Perth and Kinross": "ta",
    "Fife": "ta",
    "Stirling": "ta",
    "Falkirk": "ta",
    "Clackmannanshire": "ta",
    "Argyll and Bute": "st",
    "East Ayrshire": "st",
    "North Ayrshire": "st",
    "South Ayrshire": "st",
    "East Dunbartonshire": "st",
    "West Dunbartonshire": "st",
    "Glasgow City": "st",
    "Inverclyde": "st",
    "North Lanarkshire": "st",
    "South Lanarkshire": "st",
    "East Renfrewshire": "st",
    "Renfrewshire": "st",
    "Dumfries and Galloway": "dg",
    "Scottish Borders": "dg",
    "City of Edinburgh": "dg",
    "East Lothian": "dg",
    "Midlothian": "dg",
    "West Lothian": "dg",
}
# The whole of the UK: noisier, but better than nothing for a place we can't place.
UK_REGION = "UK"


def region_for(location: Postcode) -> str:
    """Find the Met Office warning region `location` is in; the whole UK if it's in none we know."""
    if location.country == "Scotland":
        region = SCOTTISH_REGION_BY_COUNCIL.get(location.admin_district or "")
    else:
        region = REGION_BY_AREA.get(location.region or location.country)
    if region is None:
        # Not the area: it says roughly where the screen, and someone's home, is.
        logger.warning("No Met Office warning region for a postcode's area; using the whole UK")
        return UK_REGION
    return region
