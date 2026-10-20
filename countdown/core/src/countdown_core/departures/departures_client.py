"""The departures panel: every stop the owner picked, London's and Norway's alike.

Each stop is read from the store, as its source's provider (TfL's or Entur's) wrote it, and drawn the same way.
"""

import logging
from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING, Any, Protocol, override

from redis.exceptions import RedisError

from countdown_api.models import ConfigResponse, DepartureStop
from countdown_core.core import rotation
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.core.style import panel_style
from countdown_core.entur.stored_source import StoredEnturSource
from countdown_core.i18n import N_
from countdown_core.notices.entur import StoredEnturSituationsSource
from countdown_core.notices.tfl import StoredTflLineStatusSource, StoredTflStationSource
from countdown_core.store import reader
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel, Rotation
from countdown_core.tfl.stored_source import NoStoredDeparturesError, StoredTflSource

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.core.style import Style
    from countdown_core.notices.notice import NoticeSource
    from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel

logger = logging.getLogger(__name__)


class StopSource[S](Protocol):
    """Where one kind of stop's details and departures come from, such as TfL's stops in the store; `S` is a stop."""

    def resolve(self, stop_id: str) -> S | None:
        """Look the stop up: None if the source doesn't know it; raises if the source can't be asked."""

    def panel(self, stop: S, style: Style) -> AbstractArrivalPanel:
        """Return the stop's departures panel; raises if it can't be read."""


@dataclass(frozen=True)
class DeparturesSettings:
    """What the departures panel is configured with.

    That's the broker's `departures` section, or its `tfl` one when there is none. Compared by value,
    so a change makes AbstractClient.needs_refresh rebuild it.
    """

    stops: tuple[DepartureStop, ...]
    style: Style = "hero"

    @classmethod
    def from_config(cls, config: ConfigResponse) -> DeparturesSettings:
        """Read the settings from the broker's config, from whichever of its sections it has."""
        if config.departures is not None:
            return cls(tuple(config.departures.stops), panel_style(config, config.departures.style))
        stops = tuple(DepartureStop(source="tfl", id=stop_id) for stop_id in config.tfl.stop_ids)
        return cls(stops, panel_style(config, config.tfl.style))

    def stop_ids(self, source: str) -> list[str]:
        """Return the ids of the stops from one source, in order."""
        return [stop.id for stop in self.stops if stop.source == source]


@dataclass(frozen=True)
class ResolvedStop:
    """A configured stop as its source knows it, ready to fetch departures for."""

    id: str
    source: StopSource
    stop: Any


class DeparturesClient(AbstractClient):
    """The departures for the configured stops, each from its own source, a page of stops at a time."""

    panel_title = N_("Arrivals")

    def __init__(self, device_id: str, config: DeparturesSettings) -> None:
        """Never touches the network: stops are resolved in _initialise(). Raises without a store to read."""
        super().__init__(device_id, config)
        # Departures come from the store (the TfL and Entur providers fetch them once for every screen); raises
        # without one. So do TfL's and Entur's disruption notices.
        store = reader.require_store()
        self.sources: dict[str, StopSource] = {"tfl": StoredTflSource(store), "entur": StoredEnturSource(store)}
        self._notice_sources = self._build_notice_sources(config)
        self.stops: list[ResolvedStop] = []
        # Set by the layout (ApiRegistry.show_stops).
        self.stops_per_update = 2

    def _build_notice_sources(self, config: DeparturesSettings) -> list[NoticeSource]:
        """Build the disruption sources for the stops: TfL's line status and stations, and Entur's situations."""
        sources: list[NoticeSource] = []
        if tfl_stops := config.stop_ids("tfl"):
            sources += [
                StoredTflLineStatusSource(self.session, tfl_stops),
                StoredTflStationSource(self.session, tfl_stops),
            ]
        if entur_stops := config.stop_ids("entur"):
            sources.append(StoredEnturSituationsSource(reader.require_store(), entur_stops))
        return sources

    @override
    def notice_sources(self) -> list[NoticeSource]:
        """Return the disruptions at the configured stops and on their lines."""
        return self._notice_sources

    @override
    def _initialise(self) -> None:
        """Resolve the configured stops. Fails (to be retried) only if none resolve."""
        if self.stops:
            return
        resolved = self.init_stops(self.config.stops)
        if self.config.stops and not resolved:
            msg = f"None of the {len(self.config.stops)} configured stops could be resolved"
            raise RuntimeError(msg)
        self.stops = resolved

    def init_stops(self, stops: tuple[DepartureStop, ...] | list[DepartureStop]) -> list[ResolvedStop]:
        """Look each stop up in its source, leaving out (and logging) any that can't be."""
        resolved: list[ResolvedStop] = []
        for stop in stops:
            source = self.sources.get(stop.source)
            if source is None:
                logger.warning("Stop %s is from %r, which this renderer doesn't know", stop.id, stop.source)
                continue
            try:
                info = source.resolve(stop.id)
            except Exception as e:
                logger.warning("Error fetching %s stop %s: %s", stop.source, stop.id, e, exc_info=True)
                continue
            if info is None:
                logger.warning("%s stop %s could not be resolved", stop.source, stop.id)
            else:
                resolved.append(ResolvedStop(stop.id, source, info))
        return resolved

    def _this_page(self) -> tuple[list[int], Rotation]:
        """Return the indexes of the stops on this minute's page, and which page it is (see core.rotation).

        Pages are fixed: page k is stops k * stops_per_update onwards, and the last one holds
        what's left. 5 stops 2 at a time are pages 0+1, 2+3 and 4.
        """
        if not self.stops:
            msg = "No stops initialised"
            raise ValueError(msg)
        pages = ceil(len(self.stops) / self.stops_per_update)
        page = rotation.page(pages)
        start = page * self.stops_per_update
        indexes = list(range(start, min(start + self.stops_per_update, len(self.stops))))
        return indexes, Rotation(page, pages)

    @override
    def _update(self) -> Panel:
        if not self.stops:
            return self.message_panel(N_("Needs configuring"))
        # All of them, in order, when they fit; otherwise this minute's page of them, with the
        # squares under the stops saying which page it is.
        shown = None
        if len(self.stops) > self.stops_per_update:
            to_fetch, shown = self._this_page()
        else:
            to_fetch = list(range(len(self.stops)))
        panels = []
        expired = 0
        for index in to_fetch:
            resolved = self.stops[index]
            try:
                # The panels group them by route, keeping the later ones for the styles that show them.
                panels.append(resolved.source.panel(resolved.stop, self.config.style))
            except reader.NoStoredDataError as e:
                # Expected, and not worth a traceback each poll: the store has nothing for it (its key has expired, or
                # the provider hasn't got to it yet). The page's other stops are drawn without it.
                logger.warning("No departures for %s: %s", resolved.id, e)
                expired += 1
            except RedisError:
                # Redis not answering isn't one stop's problem: raised, so the render fails, keeping the screen's frame.
                raise
            except Exception:
                logger.exception("Error fetching departures for %s", resolved.id)
        if not panels:
            msg = f"Could not fetch departures for any of {len(to_fetch)} stops"
            if expired == len(to_fetch):
                # Every stop's key has expired: the panel says the departures aren't available, never old ones.
                raise NoStoredDeparturesError(msg)
            # Every stop failed some other way (a bug): its area says it could not connect.
            raise RuntimeError(msg)
        return CombinedArrivalPanel(panels, shown)
