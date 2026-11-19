"""A TfL stop's coming timetabled departures in the store: the value of `v1:timetable:<stop id>` (keys.timetable).

TfL's Arrivals only predicts about half an hour ahead, so while the Tube is shut a station has none at all. The TfL
timetable provider (countdown_providers.tfl_timetable) works out from each of the stop's lines' timetables the next
departures, the first few per line and destination, and writes them here every 15 minutes; the TfL provider reads them
and adds a line's to the stop's departures, marked `scheduled`, while TfL predicts nothing for that line there.

    {"fetched_at": "2026-11-19T04:00:00Z", "stop_id": "940GZZLUKNG", "departures": [{"line": "Northern",
     "line_id": "northern", "destination": "Morden Underground Station", "departs_at": "2026-11-19T05:42:00Z"}, ...]}
"""

from pydantic import AwareDatetime, BaseModel, Field

from countdown_shared.store.models import Stored


class TimetabledDeparture(BaseModel):
    """One departure the line's timetable has from the stop."""

    # TfL's name for the line ("Northern", "393"), as its Arrivals' lineName has it.
    line: str
    # TfL's id for it ("northern", "393").
    line_id: str
    # As TfL names the journey's last stop ("Morden Underground Station"): the panel decides how to shorten it.
    destination: str
    departs_at: AwareDatetime


class StoredTimetable(Stored):
    """The value of `v1:timetable:<stop id>`: the stop's next timetabled departures, soonest first."""

    stop_id: str
    departures: list[TimetabledDeparture] = Field(default_factory=list)
