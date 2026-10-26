"""What the MetAlerts provider stores for the notice board's Norwegian weather warnings, and the renderer reads back.

MET Norway's own answers, as they came: MetAlerts' current.json (a GeoJSON FeatureCollection, one Feature per warning,
each with the area it covers as its geometry), asked for without a place, so for all of Norway, once in each language
the screens are drawn in. The renderer picks out the warnings covering its own place, and words them, when it draws
(countdown_shared.notices.norway.StoredMetAlertsSource). tests/fixtures/metalerts_current.json is an example, which both
the provider's tests and the renderer's read.
"""

from typing import Any

from countdown_shared.store.models import Stored


class StoredMetAlerts(Stored):
    """MET Norway's current weather warnings for all of Norway: the value of `v1:notices:metalerts` (TTL ~30 min).

    `fetched_at` is when MET last sent, or confirmed unchanged, the older of the two answers.
    """

    # current.json?lang=no: the warnings worded in Norwegian, for a screen in Norwegian (nb).
    no: dict[str, Any]
    # current.json?lang=en: in English, for every other screen.
    en: dict[str, Any]
