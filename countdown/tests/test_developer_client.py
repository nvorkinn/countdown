import asyncio
import datetime as dt

import pytest
import responses

from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.models import AiPlan
from countdown_core.notices.notice import Severity
from countdown_credentials.registration import RendererRegistration

BROKER_URL = "https://broker.example.com"
DEVELOPER_URL = f"{BROKER_URL}/api/developer"

ANSWER = {
    "github": {"login": "nvorkinn", "repos": 9, "open_prs": 3, "review_requests": 1, "failing": ["auth-broker"]},
    "plans": [
        {"name": "Copilot", "plan": "Pro", "renews": "2026-11-01", "used": 280, "limit": 300},
        {"name": "Claude", "plan": "Max", "renews": "2026-10-18", "used": None, "limit": None},
        {"name": "Gemini", "plan": "AI Pro", "renews": "2026-10-17"},
    ],
}
# Midday in London on 17 October 2026.
NOW = dt.datetime(2026, 10, 17, 11, 0, tzinfo=dt.UTC)


@pytest.fixture
def client() -> DeveloperClient:
    return DeveloperClient(
        RendererRegistration(BROKER_URL, "shh", "device-123"), DeveloperSettings(enabled=True, style="card")
    )


@responses.activate
def test_update_draws_the_brokers_answer_in_the_configured_style(client):
    responses.add(responses.GET, DEVELOPER_URL, json=ANSWER)

    panel = asyncio.run(client.update())

    assert isinstance(panel, DeveloperPanel)
    assert panel.style == "card"
    assert panel.summary.github.failing == ["auth-broker"]
    assert panel.summary.plans[0].renews == dt.date(2026, 11, 1)


@responses.activate
def test_update_sends_the_device_secret_as_a_bearer_token(client):
    responses.add(responses.GET, DEVELOPER_URL, json=ANSWER)

    asyncio.run(client.update())

    assert responses.calls[0].request.headers["Authorization"] == "Bearer shh"


@responses.activate
def test_a_broker_without_a_github_token_still_gives_the_plans(client):
    responses.add(responses.GET, DEVELOPER_URL, json=ANSWER | {"github": None})

    panel = asyncio.run(client.update())

    assert panel.summary.github is None
    assert len(panel.summary.plans) == 3


@responses.activate
def test_a_failing_broker_raises_so_the_last_panel_stays(client):
    responses.add(responses.GET, DEVELOPER_URL, status=500)

    with pytest.raises(Exception):  # noqa: B017, PT011 -- whatever requests raises; the registry keeps the last panel
        asyncio.run(client.update())


def test_no_notices_before_the_first_answer(client):
    (source,) = client.notice_sources()

    assert source.fetch(NOW) == []


@responses.activate
def test_notices_for_failing_builds_renewals_and_a_plan_nearly_used_up(client):
    responses.add(responses.GET, DEVELOPER_URL, json=ANSWER)
    asyncio.run(client.update())
    (source,) = client.notice_sources()

    notices = [(n.source, n.severity, n.text) for n in source.fetch(NOW)]

    assert notices == [
        ("GitHub", Severity.WARNING, "auth-broker: build failing"),
        ("Copilot", Severity.INFO, "93% of Pro used"),
        ("Claude", Severity.REMINDER, "Max renews tomorrow"),
        ("Gemini", Severity.REMINDER, "AI Pro renews today"),
    ]


def test_the_notice_source_asks_nothing_itself(client):
    """So the notice board doesn't take its answers as a sign it's online."""
    (source,) = client.notice_sources()

    assert source.needs_network is False


@pytest.mark.parametrize(
    ("used", "limit", "share"),
    [(None, None, None), (150, 300, 0.5), (10, 0, None), (330, 300, 1.1)],
)
def test_share_used(used, limit, share):
    plan = AiPlan(name="Copilot", plan="Pro", renews=dt.date(2026, 11, 1), used=used, limit=limit)

    assert plan.share_used == share
