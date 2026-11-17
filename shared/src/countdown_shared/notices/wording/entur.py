"""The wording of Entur's situation messages: disruptions at Norwegian stops and on the lines leaving them.

Made from each stop's answer as provider-entur-situations stored it (store/situations.py), as of the moment asked
about and in the screen's language. Reading the store is the caller's.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from countdown_shared.i18n import _, language
from countdown_shared.notices.wording.notice import Notice, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from countdown_shared.store.situations import StoredSituations

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
