"""What the broker asks the orchestrator for: one renderer per screen that has been set up."""

import re
from dataclasses import dataclass
from enum import StrEnum

# What a device name may be on the broker, in logs and as a metric label (see countdown_credentials.device_name),
# but also a name the user picks, so it must start with a letter or digit and has a length limit.
SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}")


class Status(StrEnum):
    """Where an assignment is, as the broker and the orchestrator tell each other."""

    PENDING = "pending"
    PROVISIONED = "provisioned"
    # The screen has been gone for a day (the broker decides when): the renderer is stopped, not deleted, so it
    # is the same device when the screen comes back. The broker then lists the assignment as pending again.
    SUSPENDED = "suspended"
    FAILED = "failed"
    DELETING = "deleting"
    # Only ever reported, never listed: the assignment and its renderer are gone.
    DELETED = "deleted"


# The states in which the screen is wanted on: drawn by its renderer, or by the render pool.
ACTIVE = frozenset({Status.PENDING, Status.PROVISIONED, Status.FAILED})


@dataclass(frozen=True)
class Assignment:
    """A screen's renderer, as the broker wants it.

    `id` is the broker's own key, which doesn't change when a slug does; the slug names the container and its
    volumes. `device_id` is the screen's, which its keys in the store are under (the render pool renders by it); None
    once the device is forgotten, or from a broker that doesn't list it yet.
    """

    id: str
    slug: str
    # None once the renderer has claimed its screen: the broker clears the token when it is used.
    claim_token: str | None
    status: Status
    device_id: str | None = None


def valid_slug(slug: str) -> bool:
    """Whether `slug` is safe to name a container and its volumes with (see SLUG_PATTERN)."""
    return SLUG_PATTERN.fullmatch(slug) is not None
