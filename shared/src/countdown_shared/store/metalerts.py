"""What the MetAlerts provider stores for the notice board's Norwegian weather warnings, and the renderer reads back.

MET Norway's own answers, as they came: MetAlerts' current.json (a GeoJSON FeatureCollection, one Feature per warning,
each with the area it covers as its geometry), asked for without a place, so for all of Norway, once in each language
the screens are drawn in (`no` and `en`). The renderer picks out the warnings covering its own place, and words them,
when it draws (countdown_renderer.notices.norway.StoredMetAlertsSource); countdown-server picks out the same for a
touchscreen's notices (getNotices).

The value is the store contract's StoredMetAlerts (countdown-api's store.yaml: `metalerts`), re-exported here.
tests/fixtures/metalerts_current.json is an example, which both the provider's tests and the renderer's read; the
contract's metalerts.json (countdown_api.fixtures) is another.
"""

from countdown_api.models import StoredMetAlerts

__all__ = ["StoredMetAlerts"]
