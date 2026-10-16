import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.tfl.models import Postcode

logger = logging.getLogger(__name__)

FEED_URL = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/{region}"
UK_TIME = ZoneInfo("Europe/London")

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

SEVERITY_BY_COLOUR = {"red": Severity.SEVERE, "amber": Severity.WARNING, "yellow": Severity.INFO}
TITLE_PATTERN = re.compile(r"^(Red|Amber|Yellow) warning of (.+?) affecting", re.IGNORECASE)
# e.g. "valid from 0600 Wed 25 Sep to 2100 Wed 25 Sep"
VALID_TO_PATTERN = re.compile(r"\bto (\d{4}) \w{3} (\d{1,2}) (\w{3})\b")


def region_for(location: Postcode) -> str:
    if location.country == "Scotland":
        region = SCOTTISH_REGION_BY_COUNCIL.get(location.admin_district or "")
    else:
        region = REGION_BY_AREA.get(location.region or location.country)
    if region is None:
        area = location.admin_district or location.region or location.country
        logger.warning(f"No Met Office warning region for {area}; using the whole UK")
        return UK_REGION
    return region


def _valid_until(description: str, now: datetime) -> datetime | None:
    """The end of a warning's validity, from the "valid from ... to 2100 Wed 25 Sep" in
    its description. The feed leaves the year out, so it's this year's date unless that's
    long gone (a warning into January, read in December)."""
    match = VALID_TO_PATTERN.search(description)
    if not match:
        return None
    time_text, day, month = match.groups()
    local_now = now.astimezone(UK_TIME)
    try:
        until = datetime.strptime(f"{local_now.year} {month} {day} {time_text}", "%Y %b %d %H%M")
    except ValueError:
        return None
    until = until.replace(tzinfo=UK_TIME)
    if until < local_now - timedelta(days=180):
        until = until.replace(year=until.year + 1)
    return until


def parse_warnings(feed: bytes, now: datetime) -> list[Notice]:
    """The feed's items as Notices. Reads the colour and hazard out of titles like "Amber
    warning of wind affecting London & South East England"; a title in any other shape is
    shown as it is rather than dropped."""
    notices = []
    for item in ET.fromstring(feed).iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not title:
            continue
        valid_until = _valid_until(description, now)
        match = TITLE_PATTERN.match(title)
        if match:
            colour, hazard = match.group(1).lower(), match.group(2).lower()
            text = f"{colour.capitalize()} warning: {hazard}"
            severity = SEVERITY_BY_COLOUR[colour]
        else:
            text, severity = title, Severity.WARNING
        if valid_until is not None:
            text += f" until {valid_until:%a}"
        notices.append(Notice("Met Office", severity, text, valid_until, display_time=valid_until))
    return notices


class MetOfficeWarningsSource(NoticeSource):
    name = "Met Office warnings"
    refresh_interval = timedelta(minutes=15)

    def __init__(self, session, location: Postcode):
        super().__init__(session)
        self.region = region_for(location)
        self.url = FEED_URL.format(region=self.region)

    def fetch(self, now: datetime) -> list[Notice]:
        response = self.session.get(self.url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return parse_warnings(response.content, now)
