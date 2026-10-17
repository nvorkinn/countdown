"""What the providers store in Redis, as JSON (`model.model_dump_json()`), and the renderers read back.

Changing a field here changes the stored format: add fields with defaults, or move to a new key prefix (see `keys`).
"""

from math import ceil
from typing import TYPE_CHECKING

from pydantic import AwareDatetime, BaseModel

if TYPE_CHECKING:
    from datetime import datetime


class Stored(BaseModel):
    """The base of every stored value: when its provider fetched it, so a reader can tell how old it is."""

    fetched_at: AwareDatetime


class Departure(BaseModel):
    """One vehicle due to leave a stop, whatever the country or mode.

    The same fields the arrival panels read from TfL's Arrival and Entur's EnturDeparture (`line`, `destination` and,
    through time_to_station(), `time_to_station`), so either source's departures can be drawn from it.
    """

    line: str
    # TfL buses' destinationName (already trimmed at the comma), the Tube's `towards`, Entur's front text.
    destination: str
    # When it's due, rather than TfL's seconds-to-go: a stored value is read some time after it's fetched.
    expected_at: AwareDatetime
    # The platform or stop pole, when the source says (Entur's quay code); None from TfL.
    platform: str | None = None

    def time_to_station(self, now: datetime) -> int:
        """Return the seconds until it's due, counted from `now` (aware), and never below 0."""
        return max(0, ceil((self.expected_at - now).total_seconds()))


class StopDepartures(Stored):
    """A stop's next departures: the value of `v1:departures:<stop id>`.

    A stop with nothing due is still written, with no departures, so "nothing due" isn't mistaken for "no data".
    """

    stop_id: str
    # As the source names it, e.g. "Kennington Underground Station": the panel decides how to shorten it.
    name: str
    # The source's own mode name: TfL's "bus" or "tube"; Entur's "bus", "tram", "metro", "rail", "water"...
    mode: str
    # The letter on a London bus stop's flag ("W"); None elsewhere.
    stop_letter: str | None = None
    # Soonest first.
    departures: list[Departure] = []
