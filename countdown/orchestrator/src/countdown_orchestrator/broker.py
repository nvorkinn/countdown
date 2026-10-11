"""The orchestrator's side of the auth broker's API (see ../../README.md for the contract)."""

import requests

from countdown_orchestrator.models import Assignment, Status

REQUEST_TIMEOUT_S = 10


class OrchestratorBroker:
    def __init__(self, broker_url: str, token: str, session: requests.Session | None = None):
        self.base_url = broker_url.rstrip("/")
        self.session = session or requests.Session()
        # Its own credential, not a device secret: it may only read and report on assignments.
        self.session.headers["Authorization"] = f"Bearer {token}"

    def assignments(self) -> list[Assignment]:
        """Every assignment that still needs the orchestrator's attention."""
        response = self.session.get(f"{self.base_url}/api/orchestrator/assignments", timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        return [
            Assignment(
                id=item["id"],
                slug=item["slug"],
                claim_token=item["claim_token"],
                status=Status(item["status"]),
            )
            for item in response.json()["assignments"]
        ]

    def report(self, assignment: Assignment, status: Status, detail: str = "") -> None:
        response = self.session.post(
            f"{self.base_url}/api/orchestrator/assignments/{assignment.id}/status",
            json={"status": status.value, "detail": detail},
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
