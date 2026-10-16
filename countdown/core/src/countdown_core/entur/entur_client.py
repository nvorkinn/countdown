"""Departures anywhere in Norway (Ruter in Oslo, and every other operator), from Entur's Journey Planner GraphQL
API. One query shape serves every mode: bus, tram, metro, rail and ferry. Entur needs no key, only an ET-Client-Name
header naming the app (https://developer.entur.org/pages-intro-authentication).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT

if TYPE_CHECKING:
    import requests

JOURNEY_PLANNER_URL = "https://api.entur.io/journey-planner/v3/graphql"
HEADERS = {"ET-Client-Name": "nvorkinn-countdown"}


class TransportMode(StrEnum):
    BUS = "bus"
    TRAM = "tram"
    METRO = "metro"
    RAIL = "rail"
    WATER = "water"
    COACH = "coach"
    AIR = "air"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, value: str | None) -> TransportMode:
        try:
            return cls((value or "").lower())
        except ValueError:
            return cls.UNKNOWN


class EnturDeparture(BaseModel):
    """One departure, mode-agnostic: bus, tram, metro and the rest alike."""

    line: str = Field(description="The line's public code, e.g. '31', '5', 'L2'")
    destination: str = Field(description="The front text, e.g. 'Sognsvann'")
    mode: TransportMode
    expected_departure_time: datetime
    # Seconds until it leaves, like TfL's timeToStation, so the arrival panels take either.
    time_to_station: int
    realtime: bool = True
    quay_code: str | None = Field(default=None, description="The platform's or stop's code, e.g. '1', 'A'")


class EnturStop(BaseModel):
    """A stop place (a whole station or stop, every platform) or a quay (one platform or stop pole)."""

    id: str
    name: str
    # A quay's own code ("A", "2"); None for a whole stop place.
    public_code: str | None = None
    transport_modes: list[TransportMode] = Field(default_factory=list)


_CALLS = """
    estimatedCalls(numberOfDepartures: $numberOfDepartures, startTime: $startTime) {
      realtime
      cancellation
      forBoarding
      aimedDepartureTime
      expectedDepartureTime
      destinationDisplay { frontText }
      quay { publicCode }
      serviceJourney { transportMode line { publicCode name transportMode } }
    }
"""

# A StopPlace, e.g. NSR:StopPlace:58381 (Majorstuen): every platform's departures together.
_STOP_PLACE_QUERY = f"""
query StopPlaceDepartures($id: String!, $numberOfDepartures: Int = 10, $startTime: DateTime) {{
  stopPlace(id: $id) {{
    id
    name
    transportMode
    {_CALLS}
  }}
}}
"""

# A Quay, e.g. NSR:Quay:7333: one platform or stop pole, so one direction.
_QUAY_QUERY = f"""
query QuayDepartures($id: String!, $numberOfDepartures: Int = 10, $startTime: DateTime) {{
  quay(id: $id) {{
    id
    name
    publicCode
    stopPlace {{ id name transportMode }}
    {_CALLS}
  }}
}}
"""


def normalize_stop_id(stop_id: str | int) -> str:
    """The id with its NSR prefix; a bare number is taken to be a StopPlace."""
    s = str(stop_id).strip()
    return s if s.startswith("NSR:") else f"NSR:StopPlace:{s}"


def _is_quay(stop_id: str) -> bool:
    return stop_id.startswith("NSR:Quay:")


def _modes(raw: Any) -> list[TransportMode]:
    raw_modes = raw if isinstance(raw, list) else [raw] if raw else []
    return [TransportMode.from_str(m) for m in raw_modes]


class EnturClient:
    """Stop lookups and departures, over a session the caller owns (an AbstractClient's retrying one)."""

    def __init__(self, session: requests.Session):
        self.session = session

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            JOURNEY_PLANNER_URL,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError("Entur: " + ", ".join(err.get("message", str(err)) for err in payload["errors"]))
        return payload.get("data") or {}

    def _stop(self, stop_id: str, departures: int, now: datetime | None = None) -> dict[str, Any] | None:
        variables: dict[str, Any] = {"id": stop_id, "numberOfDepartures": departures}
        if now is not None:
            variables["startTime"] = now.isoformat()
        data = self.query(_QUAY_QUERY if _is_quay(stop_id) else _STOP_PLACE_QUERY, variables)
        stop = data.get("quay" if _is_quay(stop_id) else "stopPlace")
        return stop if isinstance(stop, dict) else None

    def get_stop_info(self, stop_id: str | int) -> EnturStop | None:
        """What the stop is called and what serves it; None if Entur doesn't know the id."""
        normalized = normalize_stop_id(stop_id)
        stop = self._stop(normalized, 1)
        if stop is None:
            return None
        if _is_quay(normalized):
            parent = stop.get("stopPlace") if isinstance(stop.get("stopPlace"), dict) else {}
            return EnturStop(
                id=str(stop.get("id") or normalized),
                name=stop.get("name") or parent.get("name") or normalized,
                public_code=stop.get("publicCode") or None,
                transport_modes=_modes(parent.get("transportMode")),
            )
        return EnturStop(
            id=str(stop.get("id") or normalized),
            name=stop.get("name") or normalized,
            transport_modes=_modes(stop.get("transportMode")),
        )

    def get_departures(self, stop_id: str | int, limit: int = 10, now: datetime | None = None) -> list[EnturDeparture]:
        """The stop's next departures, soonest first, leaving out cancelled ones and any that have gone."""
        ref_time = now or datetime.now(UTC)
        stop = self._stop(normalize_stop_id(stop_id), limit, ref_time)
        calls = stop.get("estimatedCalls") if stop else None
        if not isinstance(calls, list):
            return []

        departures: list[EnturDeparture] = []
        for call in calls:
            # Cancelled, or terminating here (nobody can board it): not a departure.
            if not isinstance(call, dict) or call.get("cancellation") or call.get("forBoarding") is False:
                continue
            service = call.get("serviceJourney") or {}
            line = service.get("line") or {}
            when = call.get("expectedDepartureTime") or call.get("aimedDepartureTime")
            if not when:
                continue
            expected = datetime.fromisoformat(when)
            seconds = int((expected - ref_time).total_seconds())
            if seconds < -60:
                continue
            departures.append(
                EnturDeparture(
                    line=line.get("publicCode") or line.get("name") or "?",
                    destination=(call.get("destinationDisplay") or {}).get("frontText") or "",
                    mode=TransportMode.from_str(line.get("transportMode") or service.get("transportMode")),
                    expected_departure_time=expected,
                    time_to_station=max(0, seconds),
                    realtime=call.get("realtime", True),
                    quay_code=(call.get("quay") or {}).get("publicCode"),
                )
            )
        departures.sort(key=lambda d: d.time_to_station)
        return departures
