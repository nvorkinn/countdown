"""The departures panel: every stop the owner picked, London's and Norway's alike, each asked of its own source and
drawn the same way."""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from countdown_api.models import ConfigResponse, DepartureStop
from countdown_core.core.abstract_client import AbstractClient
from countdown_core.core.panel import Panel
from countdown_core.entur.entur_source import EnturSource
from countdown_core.tfl.abstract_arrival_panel import AbstractArrivalPanel, Style
from countdown_core.tfl.combined_arrival_panel import CombinedArrivalPanel
from countdown_core.tfl.tfl_client import TflSource
from countdown_credentials.registration import Registration

logger = logging.getLogger(__name__)


class StopSource(Protocol):
    def resolve(self, stop_id: str) -> Any | None:
        """The stop, or None if the source doesn't know it; raises if the source can't be asked."""

    def panel(self, stop: Any, style: Style) -> AbstractArrivalPanel:
        """The stop's departures; raises if they can't be fetched."""


@dataclass(frozen=True)
class DeparturesSettings:
    """What the departures panel is configured with: the broker's `departures` section, or, from a broker that
    predates it, its `tfl` one. Compared by value, so a change makes AbstractClient.needs_refresh rebuild it."""

    stops: tuple[DepartureStop, ...]
    style: Style = "classic"
    tfl_app_key: str = ""

    @classmethod
    def from_config(cls, config: ConfigResponse) -> DeparturesSettings:
        if config.departures is not None:
            return cls(tuple(config.departures.stops), config.departures.style, config.tfl.app_key)
        stops = tuple(DepartureStop(source="tfl", id=stop_id) for stop_id in config.tfl.stop_ids)
        return cls(stops, config.tfl.style, config.tfl.app_key)

    def stop_ids(self, source: str) -> list[str]:
        """The ids of the stops from one source, in order."""
        return [stop.id for stop in self.stops if stop.source == source]


@dataclass(frozen=True)
class ResolvedStop:
    id: str
    source: StopSource
    stop: Any


class DeparturesClient(AbstractClient):
    panel_title = "Arrivals"

    def __init__(self, registration: Registration, config: DeparturesSettings):
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

    def _initialise(self) -> None:
        """Resolves the configured stops. Fails (to be retried) only if none resolve."""
        if self.stops:
            return
        resolved = self.init_stops(self.config.stops)
        if self.config.stops and not resolved:
            raise RuntimeError(f"None of the {len(self.config.stops)} configured stops could be resolved")
        self.stops = resolved

    def init_stops(self, stops: tuple[DepartureStop, ...] | list[DepartureStop]) -> list[ResolvedStop]:
        resolved: list[ResolvedStop] = []
        for stop in stops:
            source = self.sources.get(stop.source)
            if source is None:
                logger.warning(f"Stop {stop.id} is from {stop.source!r}, which this renderer doesn't know")
                continue
            try:
                info = source.resolve(stop.id)
            except Exception as e:
                logger.warning(f"Error fetching {stop.source} stop {stop.id}: {e}", exc_info=True)
                continue
            if info is None:
                logger.warning(f"{stop.source} stop {stop.id} could not be resolved")
            else:
                resolved.append(ResolvedStop(stop.id, source, info))
        return resolved

    def _get_next_stop(self) -> ResolvedStop:
        if not self.stops:
            raise ValueError("No stops initialised")
        stop = self.stops[self.current_stop]
        self.current_stop = (self.current_stop + 1) % len(self.stops)
        return stop

    def _update(self) -> Panel:
        if not self.stops:
            return self.message_panel("Needs configuring")
        # All of them, in order, when they fit; otherwise the next page of them.
        if len(self.stops) <= self.stops_per_update:
            to_fetch = list(self.stops)
        else:
            to_fetch = [self._get_next_stop() for _ in range(self.stops_per_update)]
        panels = []
        for resolved in to_fetch:
            try:
                # The panels group them by route, keeping the later ones for the styles that show them.
                panels.append(resolved.source.panel(resolved.stop, self.config.style))
            except Exception as e:
                logger.exception(f"Error fetching departures for {resolved.id}: {e}")
        if not panels:
            # Raised rather than shown, so the registry keeps the last good departures.
            raise RuntimeError(f"Could not fetch departures for any of {len(to_fetch)} stops")
        return CombinedArrivalPanel(panels)
