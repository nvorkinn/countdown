"""What the floods provider stores for the notice board, and the renderer reads back.

The Environment Agency's own answer: the items of its /flood-monitoring/id/floods for the flood areas near the
postcode, in the EA's names, with `fetched_at` beside it. The renderer makes the board's notices from it as it reads
it (countdown_core.notices.floods), so their text is in the screen's language of the moment. tests/fixtures/floods.json
is an example, from the EA's answers to the stored value.
"""

from countdown_core.notices.models import FloodWarningsResponse  # noqa: TC001 -- pydantic needs it at runtime
from countdown_core.store.models import Stored


class StoredFloods(Stored):
    """The value of `v1:notices:floods:<place>` (TTL ~30 min): the EA's warnings near a postcode, as of `fetched_at`.

    A postcode with nothing in force is still written, with no items, so "all clear" isn't mistaken for "no data".
    """

    # As the broker subscribes to it, e.g. "SE17 3LL".
    postcode: str
    # Every warning the EA lists for the flood areas near it, those no longer in force too, in the EA's order.
    floods: FloodWarningsResponse
