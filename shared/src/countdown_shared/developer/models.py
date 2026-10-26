"""What the developer panel shows: the owner's GitHub at a glance and their AI plans, as the store holds them."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

type JobState = Literal["done", "failed", "running", "approve", "queued", "skipped"]


class ReleaseJob(BaseModel):
    """One node of a release's graph: a job, or a matrix's jobs together, and the jobs it waits for."""

    key: str  # its id in the workflow file, which `needs` refers to
    name: str  # what the graph calls it
    state: JobState  # approve: waiting for the owner to approve a deployment
    needs: list[str] = []
    done: int | None = None  # of a node of several jobs: how many have finished, of `total`
    total: int | None = None


class Release(BaseModel):
    """A Release or Deploy workflow run, running or just finished, with its jobs as GitHub's graph."""

    repo: str
    title: str  # the tag it was pushed for, or its run number
    state: Literal["running", "done", "failed"]
    started: datetime
    finished: datetime | None = None
    jobs: list[ReleaseJob]

    def minutes(self, now: datetime) -> int:
        """Return how many minutes it ran for, or has run so far."""
        return max(0, round(((self.finished or now) - self.started).total_seconds() / 60))

    @property
    def waiting(self) -> list[str]:
        """The names of the jobs waiting for the owner's approval."""
        return [job.name for job in self.jobs if job.state == "approve"]


class GitHubSummary(BaseModel):
    """The owner's GitHub account at a glance."""

    login: str
    repos: int
    open_prs: int  # the owner's own pull requests still open
    review_requests: int  # open pull requests waiting for the owner's review
    failing: list[str]  # the repos whose latest run on their default branch failed
    releases: list[Release] = []  # running or just finished, the latest started first


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

    @property
    def releases(self) -> list[Release]:
        """The releases running or just finished, the latest started first."""
        return [] if self.github is None else self.github.releases
