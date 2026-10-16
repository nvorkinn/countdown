"""Disruptions at the Norwegian stops the screen shows departures for: Entur's situation messages (Ruter's and every
other operator's), on the stops themselves and on the lines leaving them.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from countdown_core.entur.entur_client import EnturClient, normalize_stop_id
from countdown_core.i18n import _, language
from countdown_core.notices.notice import Notice, NoticeSource, Severity

logger = logging.getLogger(__name__)

_SITUATION = """
      summary { value language }
      validityPeriod { endTime }
      severity
"""

_QUERY = f"""
query StopSituations($id: String!) {{
  stopPlace(id: $id) {{ name situations {{ {_SITUATION} }} estimatedCalls(numberOfDepartures: 20) {{
    serviceJourney {{ line {{ publicCode }} }} situations {{ {_SITUATION} }} }} }}
}}
"""

_QUAY_QUERY = f"""
query QuaySituations($id: String!) {{
  quay(id: $id) {{ name situations {{ {_SITUATION} }} estimatedCalls(numberOfDepartures: 20) {{
    serviceJourney {{ line {{ publicCode }} }} situations {{ {_SITUATION} }} }} }}
}}
"""

# Entur's severities (SIRI's): the worst ones are a warning, the rest just worth knowing.
WARNING_SEVERITIES = {"severe", "verySevere"}


def _text(texts: list[dict[str, Any]]) -> str:
    """The summary in the screen's language if Entur has it in that one, else whichever it has first."""
    wanted = {"nb": ("nob", "no", "nb"), "en": ("eng", "en")}[language()]
    for text in texts:
        if (text.get("language") or "").lower() in wanted and text.get("value"):
            return text["value"]
    return next((text["value"] for text in texts if text.get("value")), "")


def _until(situation: dict[str, Any]) -> datetime | None:
    end = (situation.get("validityPeriod") or {}).get("endTime")
    return datetime.fromisoformat(end) if end else None


class EnturSituationsSource(NoticeSource):
    """One notice per situation, tagged with the stop or the line it's about, said once however many departures it
    affects.
    """

    name = "Entur disruptions"
    refresh_interval = timedelta(minutes=5)

    def __init__(self, session, stop_ids: list[str]):
        super().__init__(session)
        self.client = EnturClient(session)
        self.stop_ids = [normalize_stop_id(stop_id) for stop_id in stop_ids]

    def fetch(self, now: datetime) -> list[Notice]:
        notices: dict[str, Notice] = {}

        def add(tag: str, situation: dict[str, Any]) -> None:
            text = " ".join(_text(situation.get("summary") or []).split())
            until = _until(situation)
            if not text or text in notices or (until is not None and until < now):
                return
            severity = Severity.WARNING if situation.get("severity") in WARNING_SEVERITIES else Severity.INFO
            notices[text] = Notice(tag, severity, text, until)

        for stop_id in self.stop_ids:
            is_quay = stop_id.startswith("NSR:Quay:")
            data = self.client.query(_QUAY_QUERY if is_quay else _QUERY, {"id": stop_id})
            stop = data.get("quay" if is_quay else "stopPlace")
            if not isinstance(stop, dict):
                logger.warning(f"Entur doesn't know stop {stop_id}")
                continue
            for situation in stop.get("situations") or []:
                add(stop.get("name") or "", situation)
            for call in stop.get("estimatedCalls") or []:
                line = ((call.get("serviceJourney") or {}).get("line") or {}).get("publicCode")
                for situation in call.get("situations") or []:
                    add(_("Line {line}").format(line=line) if line else stop.get("name") or "", situation)
        return list(notices.values())
