"""Disruptions at the Norwegian stops the screen shows departures for.

These are Entur's situation messages (Ruter's and every other operator's), on the stops themselves and on the lines
leaving them. provider-entur-situations asks Entur for each stop's (entur_feed.query_situations) and stores the answer
(`v1:notices:entur:<stop id>`); the notice board reads it from there (StoredEnturSituationsSource) and makes the
notices as it draws (situation_notices).
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, override

from countdown_core.i18n import _, language
from countdown_core.notices.notice import Notice, NoticeSource, Severity
from countdown_core.store import keys, reader
from countdown_core.store.situations import StoredSituations

if TYPE_CHECKING:
    from collections.abc import Iterable

    import redis

logger = logging.getLogger(__name__)

# Entur's severities (SIRI's): the worst ones are a warning, the rest just worth knowing.
WARNING_SEVERITIES = {"severe", "verySevere"}


def _text(texts: list[dict[str, Any]]) -> str:
    """Pick the summary in the screen's language if Entur has it in that one, else whichever it has first."""
    wanted = {"nb": ("nob", "no", "nb"), "en": ("eng", "en")}[language()]
    for text in texts:
        if (text.get("language") or "").lower() in wanted and text.get("value"):
            return text["value"]
    return next((text["value"] for text in texts if text.get("value")), "")


def _until(situation: dict[str, Any]) -> datetime | None:
    end = (situation.get("validityPeriod") or {}).get("endTime")
    return datetime.fromisoformat(end) if end else None


def situation_notices(answers: Iterable[StoredSituations], now: datetime) -> list[Notice]:
    """Make the notices from the stops' answers, in order, as of `now` and in the screen's language.

    One per situation, tagged with the stop or the line it's about, and said once however many stops and departures
    it affects. A situation that ended before `now` is left out.
    """
    notices: dict[str, Notice] = {}

    def add(tag: str, situation: dict[str, Any]) -> None:
        text = " ".join(_text(situation.get("summary") or []).split())
        until = _until(situation)
        if not text or text in notices or (until is not None and until < now):
            return
        severity = Severity.WARNING if situation.get("severity") in WARNING_SEVERITIES else Severity.INFO
        notices[text] = Notice(tag, severity, text, until)

    for answer in answers:
        stop = answer.stop()
        if stop is None:
            logger.warning("Entur doesn't know stop %s", answer.stop_id)
            continue
        for situation in stop.get("situations") or []:
            add(stop.get("name") or "", situation)
        for call in stop.get("estimatedCalls") or []:
            line = ((call.get("serviceJourney") or {}).get("line") or {}).get("publicCode")
            for situation in call.get("situations") or []:
                add(_("Line {line}").format(line=line) if line else stop.get("name") or "", situation)
    return list(notices.values())


class NoStoredSituationsError(reader.NoStoredDataError):
    """None of the screen's Entur stops has its situations in the store: their provider is down, or not there yet."""


class StoredEnturSituationsSource(NoticeSource):
    """The disruptions at the screen's Entur stops, from the store rather than from Entur.

    provider-entur-situations fetches each stop's situations for every screen (`v1:notices:entur:<stop id>`); this
    makes the notices from them each time the board asks, so their language and which have ended are as of then.
    """

    name = "Entur disruptions"

    def __init__(self, store: redis.Redis, stop_ids: list[str]) -> None:
        """Read from `store`; `stop_ids` are the configured stop places or quays, as `v1:sub:entur` has them."""
        # Never asks Entur itself, so it has no session (NoticeSource.__init__).
        self.store = store
        self.stop_ids = list(stop_ids)

    @override
    def fetch(self, now: datetime) -> list[Notice]:
        """Make the notices from what the store has for the stops, leaving out (and logging) any it has nothing for.

        Raises NoStoredSituationsError if it has nothing for any of them, so the board leaves it out; a RedisError if
        the store can't be asked.
        """
        if not self.stop_ids:
            return []
        values = self.store.mget([keys.entur_situations(stop_id) for stop_id in self.stop_ids])
        missing = [stop_id for stop_id, value in zip(self.stop_ids, values, strict=True) if value is None]
        if len(missing) == len(self.stop_ids):
            msg = f"No situations in the store for Entur stops {', '.join(missing)}"
            raise NoStoredSituationsError(msg)
        if missing:
            # Its provider hasn't got to it yet, or couldn't fetch it: the other stops' still count.
            logger.warning("No situations in the store for Entur stops %s", ", ".join(missing))
        return situation_notices([StoredSituations.model_validate_json(v) for v in values if v is not None], now)
