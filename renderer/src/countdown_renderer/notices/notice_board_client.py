"""The notice board client: which sources a screen gets, polling them, and ranking what they say."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, override

from redis.exceptions import RedisError

from countdown_renderer import moment
from countdown_renderer.abstract_client import NOT_AVAILABLE, AbstractClient
from countdown_renderer.notices.calendar import ClockChangeSource, StoredBankHolidaySource
from countdown_renderer.notices.floods import StoredFloodWarningsSource
from countdown_renderer.notices.met_office import StoredMetOfficeWarningsSource
from countdown_renderer.notices.norway import NorwegianHolidaySource, StoredMetAlertsSource
from countdown_renderer.notices.notice_board_panel import NoticeBoardPanel
from countdown_renderer.notices.tfl import StoredTflRoadSource
from countdown_shared.i18n import N_, local_zone
from countdown_shared.store import keys, reader
from countdown_shared.store.location import StoredLocation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from countdown_renderer.notices.notice import Notice, NoticeSource
    from countdown_renderer.style import Style
    from countdown_renderer.system_screens.message_panel import MessagePanel
    from countdown_shared.tfl.models import Postcode

logger = logging.getLogger(__name__)

# How long to wait before reading the postcode's location from the store again when it isn't there (yet): a poll, as a
# read costs nothing upstream, and provider-locations writes a new postcode's within seconds of the config's save.
LOCATION_RETRY = timedelta(minutes=1)


@dataclass(frozen=True)
class NoticeBoardSettings:
    """What the notice board is configured with: the broker's `notice_board` section.

    The board's own sources are about the postcode's place; the disruptions at the stops come from the departures
    client (see use_client_sources). Compared by value, so a change makes AbstractClient.needs_refresh rebuild the
    board.
    """

    postcode: str | None
    # The postcode's country; None if the broker sends none, and the postcode is then British.
    country: str | None = None
    # How the board is drawn (the config's style, see panel_style); hero if the broker sends neither.
    style: Style = "hero"


class NoticeBoardClient(AbstractClient[NoticeBoardSettings]):
    """Reads every notice source on every poll, and shows what they report, most severe first.

    Its own sources are about the postcode's place (holidays, weather warnings, floods, roads); the other clients
    hand it theirs, about what they show (the departures' disruptions at their stops). They're all reads of the store
    or sums worked out here, and nothing they said before is kept: a source whose key has expired adds nothing.
    """

    panel_title = N_("Notices")

    def __init__(self, device_id: str, config: NoticeBoardSettings) -> None:
        """Build the sources `config` asks for, for the screen `device_id`."""
        super().__init__(device_id, config)
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
        # Whether the store had no location for the postcode when last asked (rather than failing to answer).
        self.location_missing = False
        self.unknown_postcode = False

    @override
    def _initialise(self) -> None:
        pass  # nothing to set up that update() can't retry on its own

    def use_client_sources(self, sources: Iterable[NoticeSource]) -> None:
        """Read `sources`, the other clients' notice sources, from now on, in place of the ones given before."""
        self.client_sources = list(sources)

    def _default_holidays(self) -> NoticeSource:
        """Pick the holidays for a screen whose postcode doesn't say where it is.

        Norway's for a Norwegian postcode or a screen on Norwegian time, otherwise England and Wales's.
        """
        if self.norwegian or local_zone().key == "Europe/Oslo":
            return NorwegianHolidaySource(self.session)
        return StoredBankHolidaySource()

    def _ensure_location(self, now: datetime) -> None:
        """Add the sources about the postcode's place once the store says where it is (provider-locations writes it).

        Until it does, a board with nothing else to show says it's not available (or, while there's no store or what's
        there can't be read, that it couldn't look the postcode up yet), and reads again after LOCATION_RETRY. Redis not
        answering raises its RedisError. A postcode the store says doesn't exist gets "Unknown postcode", for good.
        """
        if not self.postcode or self.location is not None:
            return
        if self.location_tried is not None and now - self.location_tried < LOCATION_RETRY:
            return
        self.location_tried = now
        self.location_missing = False
        try:
            stored = reader.read(reader.require_store(), keys.location(self.postcode), StoredLocation)
        except RedisError:
            # Redis not answering, as for the sources (_read): the render fails, keeping the screen's frame, rather than
            # draw the board without its local notices.
            raise
        except Exception as e:  # noqa: BLE001 -- no store, a provider on another version: tried again
            logger.warning("Couldn't read the postcode's location from the store: %s", type(e).__name__)
            return
        if stored is None:
            self.location_missing = True
            # Not the key: where the board is stays out of the logs.
            logger.warning("No location in the store for the board's postcode yet (is provider-locations running?)")
            return
        location = stored.location
        if location is None:
            country = "Norwegian" if self.norwegian else "UK"
            logger.warning("The configured postcode isn't a known %s postcode; no local notices", country)
            self.postcode = None  # it doesn't exist: nothing more to read about it
            self.unknown_postcode = True
            self.sources.append(self._default_holidays())
            return
        self.location = location
        if self.norwegian:
            self.sources += [NorwegianHolidaySource(self.session), StoredMetAlertsSource(self.session, location)]
        else:
            self.sources += [
                StoredBankHolidaySource(location.country),
                StoredMetOfficeWarningsSource(self.session, location, self.postcode),
                StoredFloodWarningsSource(self.session, self.postcode),
                StoredTflRoadSource(self.session, self.postcode),
            ]
        # Otherwise a quiet board gives no sign the postcode was placed. Nothing about where: not its area, region or
        # bank holidays' division either, as each says roughly where the board, and someone's home, is. Debug, not
        # info: the board is built afresh for every render, and a frame gets no log line of its own.
        logger.debug("Notice board: found the postcode's location; reading its local notices")

    def _read(
        self, sources: list[NoticeSource], now: datetime
    ) -> tuple[dict[NoticeSource, list[Notice]], set[NoticeSource]]:
        """Ask every source what it says as of `now`: the answers by source, and the sources whose keys have expired.

        A source that fails otherwise is logged and left out of both. Redis not answering raises (RedisError), so the
        render fails, keeping the screen's frame: that's not the data expiring.
        """
        answers: dict[NoticeSource, list[Notice]] = {}
        expired: set[NoticeSource] = set()
        for source in sources:
            try:
                answers[source] = source.fetch(now)
            except RedisError:
                raise
            except reader.NoStoredDataError as e:
                # Its key has expired (its provider is down), or isn't there yet: expected, so not worth a traceback
                # each poll. It adds nothing.
                logger.warning("%s: %s", source.name, e)
                expired.add(source)
            except Exception as e:
                # A bug, or a provider on another version: it adds nothing either, and the others carry on regardless.
                logger.warning("%s: %s", source.name, e, exc_info=True)
        return answers, expired

    @override
    def _update(self) -> MessagePanel | NoticeBoardPanel:
        """Return what every source says now, most severe first; "Not available" if all their keys have expired.

        Raises if Redis doesn't answer (the render then fails, keeping the screen's frame), or if none of the sources
        that read the store could be read for some other reason.
        """
        now = moment.now()
        self._ensure_location(now)
        # The other clients' first, so their disruptions rank above the place's notices of the same severity.
        sources = self.client_sources + self.sources
        answers, expired = self._read(sources, now)

        online = [source for source in sources if source.needs_network]
        if online and not any(source in answers for source in online):
            if all(source in expired for source in online):
                # Every source with data to read has none: "All clear" would be a guess.
                self.expired = True
                return self.message_panel(NOT_AVAILABLE)
            msg = "None of the notice sources could be reached"
            raise RuntimeError(msg)

        order = {source: i for i, source in enumerate(sources)}
        active = [
            (notice, order[source])
            for source, notices in answers.items()
            for notice in notices
            if notice.valid_until is None or notice.valid_until > now
        ]
        if not active:
            if self.postcode and self.location is None:
                # The postcode isn't placed yet, so the weather, floods and roads haven't
                # been checked: "All clear" would be a guess.
                if self.location_missing:
                    self.expired = True
                    return self.message_panel(NOT_AVAILABLE)
                msg = "Couldn't look up the postcode yet"
                raise RuntimeError(msg)
            if self.unknown_postcode:
                return self.message_panel(N_("Unknown postcode"))
            return NoticeBoardPanel([], self.style)  # all clear: the layout leaves the notices out
        active.sort(key=lambda pair: (pair[0].severity, pair[1]))
        return NoticeBoardPanel([notice for notice, _ in active], self.style)
