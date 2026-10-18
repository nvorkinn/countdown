"""What auth-broker's developer provider stores for the developer panel, and the renderer reads back.

The provider runs in auth-broker (broker/developer_provider.py), which can't import this, so the contract is the JSON
itself: the GitHub summary and the AI plans (countdown_core.developer.models) as the broker's services/developer.py
builds them, with `fetched_at` beside it. tests/fixtures/developer.json is an example, copied into both repositories'
tests so neither side can drift alone. The broker has no route for the panel: the store is the renderer's only source.

`github` is None until the owner adds a GitHub token.
"""

from countdown_core.developer.models import DeveloperSummary
from countdown_core.store.models import Stored


class StoredDeveloper(Stored, DeveloperSummary):
    """The value of `v1:developer:<device id>` (TTL ~15 min): the developer panel's data, as of `fetched_at`."""
