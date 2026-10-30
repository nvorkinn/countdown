"""Parcels on their way: the owner's Parcel deliveries, from the store (written by the parcel provider).

The renderer never asks Parcel itself, nor holds an API key; the provider does, and normalises Parcel's status codes to
the contract's ParcelStatus, so this only sees those.
"""

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource, Severity
from countdown_shared.i18n import _, local_zone, ngettext
from countdown_shared.store import parcels, reader

if TYPE_CHECKING:
    from countdown_api.models import Parcel, ParcelList

# Not "arriving today": delivered ones stay in the list for a few hours, an exception needs the owner rather than a
# reminder of when it's due, and one out for delivery has its own notice.
NOT_DUE = frozenset({"delivered", "exception", "out_for_delivery"})


def _summary(group: list[Parcel], one: str, several: str) -> str:
    """Return `one` with the parcel's description if there's just one, else `several` with how many."""
    if len(group) == 1:
        return one.format(description=group[0].description)
    return several.format(n=len(group))


def parcel_notices(stored: ParcelList, now: datetime) -> list[Notice]:
    """Return the notices for the parcels out for delivery, and those expected today (in the screen's time zone).

    Several parcels in one state are one notice with their count, so a busy day doesn't fill the board. A parcel out for
    delivery is in the first only, whatever day Parcel expects it.
    """
    zone = local_zone()
    today = now.astimezone(zone).date()
    out = [parcel for parcel in stored.parcels if parcel.status == "out_for_delivery"]
    due = [parcel for parcel in stored.parcels if parcel.expected_date == today and parcel.status not in NOT_DUE]
    notices = []
    if out:
        text = _summary(
            out,
            _("Out for delivery: {description}"),
            ngettext("{n} parcel out for delivery", "{n} parcels out for delivery", len(out)),
        )
        notices.append(Notice(_("Parcels"), Severity.INFO, text))
    if due:
        text = _summary(
            due,
            _("Parcel arriving today: {description}"),
            ngettext("{n} parcel arriving today", "{n} parcels arriving today", len(due)),
        )
        midnight = datetime.combine(today + timedelta(days=1), time.min, zone)
        notices.append(Notice(_("Parcels"), Severity.REMINDER, text, valid_until=midnight))
    return notices


class StoredParcelsSource(NoticeSource):
    """Parcels out for delivery, and parcels expected today, from the store.

    That's `v1:parcels:<device id>`, read each time the board asks: so it says what the store has now, and nothing once
    the key has lapsed.
    """

    name = "Parcels"

    def __init__(self, device_id: str) -> None:
        """Read the device `device_id`'s parcels from the store (no HTTP, so no session)."""
        self.device_id = device_id

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Return the notices for what's stored.

        Raises NoStoredParcelsError if there's none, so the board leaves them out, RedisError if it can't ask, and
        reader.StoreNotConfiguredError if the renderer has no store.
        """
        return parcel_notices(parcels.read(reader.require_store(), self.device_id), now)
