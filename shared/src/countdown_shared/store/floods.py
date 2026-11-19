"""What the floods provider stores for the notice board, and the renderer reads back.

The Environment Agency's own answer: the items of its /flood-monitoring/id/floods for the flood areas near the
postcode, in the EA's names, with `fetched_at` beside it. The renderer makes the board's notices from it as it reads
it (countdown_renderer.notices.floods), so their text is in the screen's language of the moment.

The value is the store contract's StoredFloods (countdown-api's store.yaml: `floods`), as countdown-server reads it too
for the touchscreens' notices (getNotices), re-exported here. tests/fixtures/floods.json is an example, from the EA's
answers to the stored value; the contract's floods.json (countdown_api.fixtures) is another.
"""

from countdown_api.models import StoredFloods

__all__ = ["StoredFloods"]
