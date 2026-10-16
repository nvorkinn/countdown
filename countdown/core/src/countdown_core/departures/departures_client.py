"""The departures panel: every stop the owner picked, London's and Norway's alike.

Each stop is asked of its own source and drawn the same way.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast, override

from countdown_api.models import ConfigResponse, DepartureStop
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.entur.entur_source import EnturSource
from countdown_core.i18n import N_
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel, Rotation
from countdown_core.tfl.tfl_client import TflSource

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Style
    from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)


class StopSource[S](Protocol):
    """Where one kind of stop's details and departures come from, such as TfL; `S` is its kind of stop."""

    def resolve(self, stop_id: str) -> S | None:
        """Look the stop up: None if the source doesn't know it; raises if the source can't be asked."""

    def panel(self, stop: S, style: Style) -> AbstractArrivalPanel:
        """Fetch the stop's departures; raises if they can't be fetched."""


def _retire_classic(style: str) -> Style:
    """Draw the retired classic style, which a broker that hasn't migrated may still send, as hero."""
    return "hero" if style == "classic" else cast("Style", style)


@dataclass(frozen=True)
class DeparturesSettings:
    """What the departures panel is configured with.

    That's the broker's `departures` section, or, from a broker that predates it, its `tfl` one. Compared by value,
    so a change makes AbstractClient.needs_refresh rebuild it.
    """

    stops: tuple[DepartureStop, ...]
    style: Style = "hero"
    tfl_app_key: str = ""

    @classmethod
    def from_config(cls, config: ConfigResponse) -> DeparturesSettings:
        """Read the settings from the broker's config, from whichever of its sections it has."""
        if config.departures is not None:
            return cls(tuple(config.departures.stops), _retire_classic(config.departures.style), config.tfl.app_key)
        stops = tuple(DepartureStop(source="tfl", id=stop_id) for stop_id in config.tfl.stop_ids)
        return cls(stops, _retire_classic(config.tfl.style), config.tfl.app_key)

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

    def __init__(self, registration: Registration, config: DeparturesSettings) -> None:
        """Never touches the network: stops are resolved in _initialise()."""
        super().__init__(registration, config)
        self.sources: dict[str, StopSource] = {
            "tfl": TflSource(self.session, config.tfl_app_key),
            "entur": EnturSource(self.session),
        }
        self.stops: list[ResolvedStop] = []
        self.current_stop = 0
        # Set by the layout (ApiRegistry.show_stops).
        self.stops_per_update = 2

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

    def _next_stop_index(self) -> int:
        """Return the index of the next stop to show, and move on past it."""
        if not self.stops:
            msg = "No stops initialised"
            raise ValueError(msg)
        index = self.current_stop
        self.current_stop = (self.current_stop + 1) % len(self.stops)
        return index

    @override
    def _update(self) -> Panel:
        if not self.stops:
            return self.message_panel(N_("Needs configuring"))
        # All of them, in order, when they fit; otherwise the next page of them.
        rotating = len(self.stops) > self.stops_per_update
        if rotating:
            to_fetch = [self._next_stop_index() for _ in range(self.stops_per_update)]
        else:
            to_fetch = list(range(len(self.stops)))
        panels = []
        shown: set[int] = set()
        for index in to_fetch:
            resolved = self.stops[index]
            try:
                # The panels group them by route, keeping the later ones for the styles that show them.
                panels.append(resolved.source.panel(resolved.stop, self.config.style))
                shown.add(index)
            except Exception:
                logger.exception("Error fetching departures for %s", resolved.id)
        if not panels:
            # Raised rather than shown, so the registry keeps the last good departures.
            msg = f"Could not fetch departures for any of {len(to_fetch)} stops"
            raise RuntimeError(msg)
        # Only when they rotate: then the squares under the stops say which are on screen.
        rotation = Rotation(frozenset(shown), len(self.stops)) if rotating else None
        return CombinedArrivalPanel(panels, rotation)
