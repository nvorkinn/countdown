"""The notice board client: which sources a screen gets, polling them, and ranking what they say."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, override

from countdown_core.core.abstract_client import AbstractClient
from countdown_core.i18n import N_, local_zone
from countdown_core.notices.calendar import BankHolidaySource, ClockChangeSource
from countdown_core.notices.floods import FloodWarningsSource
from countdown_core.notices.location import lookup_postcode
from countdown_core.notices.met_office import StoredMetOfficeWarningsSource
from countdown_core.notices.norway import MetAlertsSource, NorwegianHolidaySource, lookup_norwegian_postcode
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.notices.tfl import StoredTflRoadSource

if TYPE_CHECKING:
    from collections.abc import Iterable

    from countdown_core.core.style import Style
    from countdown_core.notices.notice import Notice, NoticeSource
    from countdown_core.system_screens.message_panel import MessagePanel
    from countdown_core.tfl.models import Postcode
    from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)

# A source that keeps failing has its last notices dropped after this long, so a delay
# that cleared hours ago isn't still on the board.
STALE_AFTER = timedelta(hours=1)
# How long to wait before trying postcodes.io again after it couldn't be reached.
LOCATION_RETRY = timedelta(minutes=10)


@dataclass(frozen=True)
class NoticeBoardSettings:
    """What the notice board is configured with: the broker's `notice_board` section.

    The board's own sources are about the postcode's place; the disruptions at the stops come from the departures
    client (see use_client_sources). Compared by value, so a change makes AbstractClient.needs_refresh rebuild the
    board.
    """

    postcode: str | None
    # The postcode's country; None for a broker that predates it, whose postcodes are all British.
    country: str | None = None
    # How the board is drawn (the config's style, see panel_style); a broker that predates both gets hero.
    style: Style = "hero"


class NoticeBoardClient(AbstractClient):
    """Polls the notice sources, each on its own refresh_interval, and shows what they report, most severe first.

    Its own sources are about the postcode's place (holidays, weather warnings, floods, roads); the other clients
    hand it theirs, about what they show (the departures' disruptions at their stops).
    """

    panel_title = N_("Notices")

    def __init__(self, registration: Registration, config: NoticeBoardSettings) -> None:
        """Build the sources `config` asks for, for the screen `registration` names."""
        super().__init__(registration, config)
        self.postcode = config.postcode
        self.norwegian = config.country == "NO"
        self.sources: list[NoticeSource] = [ClockChangeSource(self.session)]
        if not config.postcode:
            self.sources.append(self._default_holidays())
        # The other clients' sources, given by the registry (use_client_sources).
        self.client_sources: list[NoticeSource] = []
        self.style = config.style
        # Location-based sources are added once the postcode is looked up (_ensure_location).
        self.location: Postcode | None = None
        self.location_tried: datetime | None = None
        self.unknown_postcode = False
        self.fetched: dict[NoticeSource, tuple[datetime, list[Notice]]] = {}
        self.last_tried: dict[NoticeSource, datetime] = {}

    @override
    def _initialise(self) -> None:
        pass  # nothing to set up that update() can't retry on its own

    def use_client_sources(self, sources: Iterable[NoticeSource]) -> None:
        """Poll `sources`, the other clients' notice sources, from now on, in place of the ones given before.

        A source that's no longer given takes its notices with it; one given again keeps them.
        """
        self.client_sources = list(sources)
        kept = set(self.client_sources) | set(self.sources)
        self.fetched = {source: answer for source, answer in self.fetched.items() if source in kept}
        self.last_tried = {source: when for source, when in self.last_tried.items() if source in kept}

    def _default_holidays(self) -> NoticeSource:
        """Pick the holidays for a screen whose postcode doesn't say where it is.

        Norway's for a Norwegian postcode or a screen on Norwegian time, otherwise England and Wales's.
        """
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
        except Exception as e:  # noqa: BLE001 -- whatever went wrong, it's tried again after LOCATION_RETRY
            logger.warning("Couldn't look up the postcode: %s", type(e).__name__)
            return
        if location is None:
            country = "Norwegian" if self.norwegian else "UK"
            logger.warning("The configured postcode isn't a known %s postcode; no local notices", country)
            self.postcode = None  # don't keep asking about a postcode that doesn't exist
            self.unknown_postcode = True
            self.sources.append(self._default_holidays())
            return
        self.location = location
        if self.norwegian:
            self.sources += [NorwegianHolidaySource(self.session), MetAlertsSource(self.session, location)]
            logger.info("Notice board: location lookup succeeded: %s", location.admin_district or "Norway")
            return
        bank_holidays = BankHolidaySource(self.session, location.country)
        met_office = StoredMetOfficeWarningsSource(self.session, location, self.postcode)
        self.sources += [
            bank_holidays,
            met_office,
            FloodWarningsSource(self.session, location),
            StoredTflRoadSource(self.session, self.postcode),
        ]
        # Otherwise a quiet board gives no sign the postcode was placed.
        area = ", ".join(part for part in (location.region or location.country, location.admin_district) if part)
        logger.info(
            "Notice board: location lookup succeeded: %s; Met Office region %s, bank holidays for %s",
            area,
            met_office.region,
            bank_holidays.division,
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
            logger.warning("%s: %s", source.name, e, exc_info=True)

    @override
    def _update(self) -> MessagePanel | NoticeBoardPanel:
        now = datetime.now(UTC)
        self._ensure_location(now)
        # The other clients' first, so their disruptions rank above the place's notices of the same severity.
        sources = self.client_sources + self.sources
        for source in sources:
            self._refresh(source, now)

        online = [source for source in sources if source.needs_network]
        if online and not any(source in self.fetched for source in online):
            msg = "None of the notice sources could be reached"
            raise RuntimeError(msg)

        order = {source: i for i, source in enumerate(sources)}
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
                msg = "Couldn't look up the postcode yet"
                raise RuntimeError(msg)
            if self.unknown_postcode:
                return self.message_panel(N_("Unknown postcode"))
            return NoticeBoardPanel([], self.style)  # all clear: the layout leaves the notices out
        active.sort(key=lambda pair: (pair[0].severity, pair[1]))
        return NoticeBoardPanel([notice for notice, _ in active], self.style)
