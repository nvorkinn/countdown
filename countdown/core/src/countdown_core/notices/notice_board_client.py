import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from countdown_api.models import Tfl
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.notices.calendar import BankHolidaySource, ClockChangeSource
from countdown_core.notices.floods import FloodWarningsSource
from countdown_core.notices.location import lookup_postcode
from countdown_core.notices.met_office import MetOfficeWarningsSource
from countdown_core.notices.notice import Notice, NoticeSource
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.notices.tfl import TflLineStatusSource, TflRoadSource, TflStationSource
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.tfl.models import Postcode
from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)

# A source that keeps failing has its last notices dropped after this long, so a delay
# that cleared hours ago isn't still on the board.
STALE_AFTER = timedelta(hours=1)
# How long to wait before trying postcodes.io again after it couldn't be reached.
LOCATION_RETRY = timedelta(minutes=15)


@dataclass(frozen=True)
class NoticeBoardSettings:
    """What the notice board is configured with: the broker's `notice_board` postcode, and its `tfl` section, for
    the stops whose lines and stations the TfL disruptions are about. Compared by value, so a change to either
    makes AbstractClient.needs_refresh rebuild the board."""

    postcode: str | None
    tfl: Tfl


class NoticeBoardClient(AbstractClient):
    """Polls the notice sources, each on its own refresh_interval, and shows what they
    currently report, most severe first."""

    panel_title = "Notices"

    def __init__(self, registration: Registration, config: NoticeBoardSettings):
        super().__init__(registration, config)
        self.postcode = config.postcode
        self.sources: list[NoticeSource] = [ClockChangeSource(self.session)]
        if not config.postcode:
            self.sources.append(BankHolidaySource(self.session))  # England and Wales
        if config.tfl.stop_ids:
            self.sources += [TflLineStatusSource(self.session, config.tfl), TflStationSource(self.session, config.tfl)]
        self.tfl_config = config.tfl
        # Location-based sources are added once the postcode is looked up (_ensure_location).
        self.location: Postcode | None = None
        self.location_tried: datetime | None = None
        self.unknown_postcode = False
        self.fetched: dict[NoticeSource, tuple[datetime, list[Notice]]] = {}
        self.last_tried: dict[NoticeSource, datetime] = {}

    def _initialise(self) -> None:
        pass  # nothing to set up that update() can't retry on its own

    def _ensure_location(self, now: datetime) -> None:
        if not self.postcode or self.location is not None:
            return
        if self.location_tried is not None and now - self.location_tried < LOCATION_RETRY:
            return
        self.location_tried = now
        try:
            location = lookup_postcode(self.session, self.postcode)
        except Exception as e:
            logger.warning(f"Couldn't look up the postcode: {type(e).__name__}")
            return
        if location is None:
            logger.warning("The configured postcode isn't a known UK postcode; no local notices")
            self.postcode = None  # don't keep asking about a postcode that doesn't exist
            self.unknown_postcode = True
            self.sources.append(BankHolidaySource(self.session))  # England and Wales
            return
        self.location = location
        bank_holidays = BankHolidaySource(self.session, location.country)
        met_office = MetOfficeWarningsSource(self.session, location)
        self.sources += [
            bank_holidays,
            met_office,
            FloodWarningsSource(self.session, location),
            TflRoadSource(self.session, self.tfl_config, location),
        ]
        # Otherwise a quiet board gives no sign the postcode was placed.
        area = ", ".join(part for part in (location.region or location.country, location.admin_district) if part)
        logger.info(
            f"Notice board: location lookup succeeded: {area}; Met Office region {met_office.region}, "
            f"bank holidays for {bank_holidays.division}"
        )

    def _refresh(self, source: NoticeSource, now: datetime) -> None:
        last = self.last_tried.get(source)
        if last is not None and now - last < source.refresh_interval:
            return
        self.last_tried[source] = now
        try:
            self.fetched[source] = (now, source.fetch(now))
        except Exception as e:
            # Keep its last notices (until they go stale); the others carry on regardless.
            logger.warning(f"{source.name}: {e}", exc_info=True)

    def _update(self) -> MessagePanel | NoticeBoardPanel:
        now = datetime.now(UTC)
        self._ensure_location(now)
        for source in self.sources:
            self._refresh(source, now)

        online = [source for source in self.sources if source.needs_network]
        if online and not any(source in self.fetched for source in online):
            raise RuntimeError("None of the notice sources could be reached")

        order = {source: i for i, source in enumerate(self.sources)}
        active = [
            (notice, order[source])
            for source, (fetched_at, notices) in self.fetched.items()
            if now - fetched_at <= max(STALE_AFTER, 3 * source.refresh_interval)
            for notice in notices
            if notice.valid_until is None or notice.valid_until > now
        ]
        if not active:
            if self.postcode and self.location is None:
                # The postcode isn't placed yet, so the weather, floods and roads haven't
                # been checked: "All clear" would be a guess.
                raise RuntimeError("Couldn't look up the postcode yet")
            if self.unknown_postcode:
                return self.message_panel("Unknown postcode")
            return NoticeBoardPanel([])  # all clear: the layout leaves the notices out
        active.sort(key=lambda pair: (pair[0].severity, pair[1]))
        return NoticeBoardPanel([notice for notice, _ in active])
