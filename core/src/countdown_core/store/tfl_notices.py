"""TfL's disruptions in the store: the values of the `v1:notices:tfl-*` keys, written by the TfL notices provider.

Each holds TfL's own answer, read with the models of TfL's answers (countdown_core.tfl.models) and written back
under TfL's names, so a value reads like what TfL sent, less the fields those models leave out. The renderer's notice
sources (countdown_core.notices.tfl) make the notices from it when they read it, so the tags are in the screen's
language and what's current is judged as of drawing, not of fetching:

    v1:notices:tfl-lines:<stop id>     {"fetched_at": ..., "stop_id": "940GZZLUKNG", "lines": [<TfL's Line>, ...]}
    v1:notices:tfl-stations:<stop id>  {"fetched_at": ..., "stop_id": "940GZZLUKNG", "disruptions": [<DisruptedPoint>]}
    v1:notices:tfl-roads:<place>       {"fetched_at": ..., "postcode": "SE17 3LL", "disruptions": [<RoadDisruption>]}

`tests/fixtures/tfl_notices.json` is a set of TfL answers, and `tfl_notices_expected.json` the notices the renderer
must make from them.
"""

from countdown_core.store.models import Stored
from countdown_core.tfl.models import (  # noqa: TC001 -- pydantic reads the annotations at runtime
    DisruptedPoint,
    Line,
    RoadDisruption,
)


class StoredLineStatus(Stored):
    """The lines serving a TfL stop, with their statuses: the value of `v1:notices:tfl-lines:<stop id>`.

    TfL's /Line/{ids}/Status answer for the lines its /StopPoint/{id} says call at the stop, in that order, every
    status included (Good Service too). A stop that isn't a bus stop or Tube station has no lines, and is still
    written, so it isn't taken for missing data.
    """

    stop_id: str
    lines: list[Line]


class StoredStationDisruptions(Stored):
    """What's wrong at a TfL stop: the value of `v1:notices:tfl-stations:<stop id>`.

    TfL's /StopPoint/{id}/Disruption answer (lifts out of order, closed entrances...), expired ones and all.
    """

    stop_id: str
    disruptions: list[DisruptedPoint]


class StoredRoadDisruptions(Stored):
    """The road disruptions near a postcode's place: the value of `v1:notices:tfl-roads:<place>`.

    The entries of TfL's /Road/all/Disruption (asked for the severities the board shows) within
    notices.tfl_feed.ROAD_RADIUS_KM of the postcode. TfL only knows London's roads, so elsewhere it's always empty.
    """

    # As the broker subscribed it, e.g. "SE17 3LL".
    postcode: str
    disruptions: list[RoadDisruption]
