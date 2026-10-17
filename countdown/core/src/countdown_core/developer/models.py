"""The broker's /api/developer answer: what the developer panel shows."""

from datetime import date

from pydantic import BaseModel


class GitHubSummary(BaseModel):
    """The owner's GitHub account at a glance."""

    login: str
    repos: int
    open_prs: int  # the owner's own pull requests still open
    review_requests: int  # open pull requests waiting for the owner's review
    failing: list[str]  # the repos whose latest run on their default branch failed


class AiPlan(BaseModel):
    """One AI subscription: its plan, when it renews, and how much of it is used where that's known."""

    name: str  # Copilot, Claude, Gemini
    plan: str  # Pro, Max, AI Pro
    renews: date
    used: int | None = None
    limit: int | None = None

    @property
    def share_used(self) -> float | None:
        """How much of the plan is used, 0 to 1 (more if over), or None if the broker can't say."""
        if self.used is None or not self.limit:
            return None
        return self.used / self.limit


class DeveloperSummary(BaseModel):
    """Everything on the developer panel; `github` is None until the owner adds a GitHub token."""

    github: GitHubSummary | None
    plans: list[AiPlan]
