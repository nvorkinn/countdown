"""What provider-entur-situations asks Entur: the situations at a stop and on the next lines leaving it, every language.

The board picks the screen's language and makes the notices as it draws (notices/entur.py).
"""

from typing import TYPE_CHECKING, Any

from countdown_core.entur.entur_client import normalize_stop_id

if TYPE_CHECKING:
    from countdown_core.entur.entur_client import EnturClient

_SITUATION = """
      summary { value language }
      validityPeriod { endTime }
      severity
"""

# Every summary's every language, so the renderer can pick the screen's as it draws.
SITUATIONS_QUERY = f"""
query StopSituations($id: String!) {{
  stopPlace(id: $id) {{ name situations {{ {_SITUATION} }} estimatedCalls(numberOfDepartures: 20) {{
    serviceJourney {{ line {{ publicCode }} }} situations {{ {_SITUATION} }} }} }}
}}
"""

QUAY_SITUATIONS_QUERY = f"""
query QuaySituations($id: String!) {{
  quay(id: $id) {{ name situations {{ {_SITUATION} }} estimatedCalls(numberOfDepartures: 20) {{
    serviceJourney {{ line {{ publicCode }} }} situations {{ {_SITUATION} }} }} }}
}}
"""


def query_situations(client: EnturClient, stop_id: str) -> dict[str, Any]:
    """Ask Entur for the situations at a stop place or quay and on the next 20 lines leaving it.

    `stop_id` is as `v1:sub:entur` has it, with or without its NSR prefix. Returns Entur's GraphQL `data` as it came
    (`{"stopPlace": None}` for a stop it doesn't know); raises on an HTTP error or a GraphQL one.
    """
    normalized = normalize_stop_id(stop_id)
    query = QUAY_SITUATIONS_QUERY if normalized.startswith("NSR:Quay:") else SITUATIONS_QUERY
    return client.query(query, {"id": normalized})
