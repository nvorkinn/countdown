"""TfL stops' panels as the renderer draws them: TfL's stop and arrivals stored as the TfL provider stores them."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from countdown_core.store.models import StopDepartures
from countdown_providers.tfl.main import to_stop_departures
from countdown_renderer.tfl.stored_source import StoredArrivalPanel, stored_panel

if TYPE_CHECKING:
    from countdown_core.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
    from countdown_renderer.style import Style

# Fetched and drawn at the same instant, so each departure is due in exactly TfL's timeToStation.
FETCHED_AT = datetime(2026, 1, 15, 12, tzinfo=UTC)


def tfl_panel(
    stop: SingleStopPoint | MetroStopPoint, arrivals: list[BusArrival] | list[TubeArrival], style: Style = "hero"
) -> StoredArrivalPanel:
    """Return the panel the renderer draws for `stop` once the provider has stored its `arrivals`."""
    stored = to_stop_departures(stop, arrivals, FETCHED_AT)
    # Read back as the renderer reads it, through the store's JSON.
    return stored_panel(StopDepartures.model_validate_json(stored.model_dump_json()), style, FETCHED_AT)
