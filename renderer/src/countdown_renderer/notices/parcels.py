"""Parcels on their way: the owner's Parcel deliveries, from the store (written by the parcel provider).

The renderer never asks Parcel itself, nor holds an API key; the provider does, and normalises Parcel's status codes to
the contract's ParcelStatus, so this only sees those.
"""

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, override

from countdown_renderer.notices.notice import Notice, NoticeSource, Severity
from countdown_shared.i18n import _, format_date, local_zone, ngettext
from countdown_shared.store import parcels, reader

if TYPE_CHECKING:
    from datetime import date

    from countdown_api.models import Parcel, ParcelList

# Each of these has its own notice, whatever day Parcel expects the parcel.
NOT_BY_DAY = frozenset({"delivered", "exception", "out_for_delivery"})


def _summary(group: list[Parcel], one: str, several: str, **values: str) -> str:
    """Return `one` with the parcel's description if there's just one, else `several` with how many."""
    if len(group) == 1:
        return one.format(description=group[0].description, **values)
    return several.format(n=len(group), **values)


def _on_the_day(day: date, group: list[Parcel], today: date) -> str:
    """Return the text for the `group` of parcels expected on `day`, today or later."""
    if day == today:
        return _summary(
            group,
            _("Parcel arriving today: {description}"),
            ngettext("{n} parcel arriving today", "{n} parcels arriving today", len(group)),
        )
    if day == today + timedelta(days=1):
        return _summary(
            group,
            _("Parcel arriving tomorrow: {description}"),
            ngettext("{n} parcel arriving tomorrow", "{n} parcels arriving tomorrow", len(group)),
        )
    return _summary(
        group,
        _("Parcel arriving {day}: {description}"),
        ngettext("{n} parcel arriving {day}", "{n} parcels arriving {day}", len(group)),
        day=format_date(day, "EEE d MMM"),
    )


def parcel_notices(stored: ParcelList, now: datetime) -> list[Notice]:
    """Return the notices for every stored parcel; the provider has already left out those the screen's settings don't.

    Several parcels in one state, or expected on one day, are one notice with their count, so a busy day doesn't fill
    the board. "Today" is the screen's, and a notice naming a day ends at its midnight, as that word changes.
    """
    zone = local_zone()
    today = now.astimezone(zone).date()
    midnight = datetime.combine(today + timedelta(days=1), time.min, zone)
    out = [parcel for parcel in stored.parcels if parcel.status == "out_for_delivery"]
    held = [parcel for parcel in stored.parcels if parcel.status == "exception"]
    delivered = [parcel for parcel in stored.parcels if parcel.status == "delivered"]
    coming = [parcel for parcel in stored.parcels if parcel.status not in NOT_BY_DAY]
    late = [parcel for parcel in coming if parcel.expected_date is not None and parcel.expected_date < today]
    undated = [parcel for parcel in coming if parcel.expected_date is None]
    by_day: dict[date, list[Parcel]] = {}
    for parcel in coming:
        if parcel.expected_date is not None and parcel.expected_date >= today:
            by_day.setdefault(parcel.expected_date, []).append(parcel)

    notices = []
    if out:
        text = _summary(
            out,
            _("Out for delivery: {description}"),
            ngettext("{n} parcel out for delivery", "{n} parcels out for delivery", len(out)),
        )
        notices.append(Notice(_("Parcels"), Severity.INFO, text))
    if held:
        text = _summary(
            held,
            _("Parcel needs attention: {description}"),
            ngettext("{n} parcel needs attention", "{n} parcels need attention", len(held)),
        )
        notices.append(Notice(_("Parcels"), Severity.INFO, text))
    if late:
        text = _summary(
            late, _("Parcel late: {description}"), ngettext("{n} parcel late", "{n} parcels late", len(late))
        )
        notices.append(Notice(_("Parcels"), Severity.REMINDER, text))
    notices += [
        Notice(_("Parcels"), Severity.REMINDER, _on_the_day(day, by_day[day], today), valid_until=midnight)
        for day in sorted(by_day)
    ]
    if undated:
        text = _summary(
            undated,
            _("Parcel on its way: {description}"),
            ngettext("{n} parcel on its way", "{n} parcels on their way", len(undated)),
        )
        notices.append(Notice(_("Parcels"), Severity.REMINDER, text))
    if delivered:
        text = _summary(
            delivered,
            _("Delivered: {description}"),
            ngettext("{n} parcel delivered", "{n} parcels delivered", len(delivered)),
        )
        notices.append(Notice(_("Parcels"), Severity.REMINDER, text))
    return notices


class StoredParcelsSource(NoticeSource):
    """The parcels the screen's settings show, from the store.

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
