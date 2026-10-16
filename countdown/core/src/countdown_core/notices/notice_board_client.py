import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from countdown_api.models import Tfl
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.departures.departures_client import DeparturesSettings
from countdown_core.i18n import N_, local_zone
from countdown_core.notices.calendar import BankHolidaySource, ClockChangeSource
from countdown_core.notices.entur import EnturSituationsSource
from countdown_core.notices.floods import FloodWarningsSource
from countdown_core.notices.location import lookup_postcode
from countdown_core.notices.met_office import MetOfficeWarningsSource
from countdown_core.notices.norway import MetAlertsSource, NorwegianHolidaySource, lookup_norwegian_postcode
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
    """What the notice board is configured with: the broker's `notice_board` section, and the departures' stops,
    whose lines and stations the disruptions are about. Compared by value, so a change to either makes
    AbstractClient.needs_refresh rebuild the board."""

    postcode: str | None
    departures: DeparturesSettings
    # The postcode's country; None for a broker that predates it, whose postcodes are all British.
    country: str | None = None

    @property
    def tfl(self) -> Tfl:
        """The TfL stops alone, for the TfL disruption sources."""
        return Tfl(app_key=self.departures.tfl_app_key, stop_ids=self.departures.stop_ids("tfl"))


class NoticeBoardClient(AbstractClient):
    """Polls the notice sources, each on its own refresh_interval, and shows what they
    currently report, most severe first."""

    panel_title = N_("Notices")

    def __init__(self, registration: Registration, config: NoticeBoardSettings):
        super().__init__(registration, config)
        self.postcode = config.postcode
        self.norwegian = config.country == "NO"
        self.sources: list[NoticeSource] = [ClockChangeSource(self.session)]
        if not config.postcode:
            self.sources.append(self._default_holidays())
        if config.tfl.stop_ids:
            self.sources += [TflLineStatusSource(self.session, config.tfl), TflStationSource(self.session, config.tfl)]
        if entur_stops := config.departures.stop_ids("entur"):
            self.sources.append(EnturSituationsSource(self.session, entur_stops))
        self.tfl_config = config.tfl
        # Location-based sources are added once the postcode is looked up (_ensure_location).
        self.location: Postcode | None = None
        self.location_tried: datetime | None = None
        self.unknown_postcode = False
        self.fetched: dict[NoticeSource, tuple[datetime, list[Notice]]] = {}
        self.last_tried: dict[NoticeSource, datetime] = {}

    def _initialise(self) -> None:
        pass  # nothing to set up that update() can't retry on its own

    def _default_holidays(self) -> NoticeSource:
        """The holidays for a screen whose postcode doesn't say where it is: Norway's for a Norwegian postcode or a
        screen on Norwegian time, otherwise England and Wales's."""
        if self.norwegian or local_zone().key == "Europe/Oslo":
            return NorwegianHolidaySource(self.session)
        return BankHolidaySource(self.session)

    def _ensure_location(self, now: datetime) -> None:
        if not self.postcode or self.location is not None:
            return
        if self.location_tried is not None and now - self.location_tried < LOCATION_RETRY:
            return
        self.location_tried = now
        lookup = lookup_norwegian_postcode if self.norwegian else lookup_postcode
        try:
            location = lookup(self.session, self.postcode)
        except Exception as e:
            logger.warning(f"Couldn't look up the postcode: {type(e).__name__}")
            return
        if location is None:
            country = "Norwegian" if self.norwegian else "UK"
            logger.warning(f"The configured postcode isn't a known {country} postcode; no local notices")
            self.postcode = None  # don't keep asking about a postcode that doesn't exist
            self.unknown_postcode = True
            self.sources.append(self._default_holidays())
            return
        self.location = location
        if self.norwegian:
            self.sources += [NorwegianHolidaySource(self.session), MetAlertsSource(self.session, location)]
            logger.info(f"Notice board: location lookup succeeded: {location.admin_district or 'Norway'}")
            return
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
            return self.message_panel(N_("Unknown postcode") if self.unknown_postcode else N_("All clear"))
        active.sort(key=lambda pair: (pair[0].severity, pair[1]))
        return NoticeBoardPanel([notice for notice, _ in active])
