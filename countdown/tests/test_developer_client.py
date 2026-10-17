"""The developer panel's client: it reads what auth-broker's developer provider stored, and never asks the broker.

tests/fixtures/developer.json is the stored contract, the same file as auth-broker's tests/fixtures/developer.json.
"""

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest
import responses
from config_factory import make_config
from test_utils import REGISTRATION

from countdown_core.core.api_registry import API_NAMES, ApiRegistry, FailedClient
from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings, NoStoredDeveloperError
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.models import AiPlan, DeveloperSummary, Release
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_core.notices.notice import Severity
from countdown_core.store import keys, reader
from countdown_credentials.registration import Registration

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "developer.json").read_text())
KEY = keys.developer("device-123")
BROKER_URL = "https://broker.example.com"

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


def _put(store, answer):
    """Store `answer` (the broker's /api/developer body) as the developer provider would, with `fetched_at`."""
    store.set(KEY, json.dumps({"fetched_at": "2026-10-17T10:59:00+00:00", **answer}))


@pytest.fixture
def client() -> DeveloperClient:
    return DeveloperClient(REGISTRATION, DeveloperSettings(enabled=True, style="card"))


@responses.activate  # nothing registered: any HTTP call would fail
def test_update_draws_the_stored_panel_in_the_configured_style(client, store):
    _put(store, ANSWER)

    panel = asyncio.run(client.update())

    assert isinstance(panel, DeveloperPanel)
    assert panel.style == "card"
    assert panel.summary.github.failing == ["auth-broker"]
    assert panel.summary.plans[0].renews == dt.date(2026, 11, 1)
    assert len(responses.calls) == 0


def test_the_shared_fixture_reads_back(client, store):
    store.set(KEY, json.dumps(FIXTURE))

    panel = asyncio.run(client.update())

    assert panel.summary.github.login == "nvorkinn"
    assert panel.summary.releases[0].title == "v0.4.3"
    assert panel.has_release


def test_parity_the_stored_panel_is_the_one_the_brokers_answer_drew(client, store):
    """The broker's old answer was the fixture without `fetched_at`: the same summary, panel and notices from it."""
    answer = {k: v for k, v in FIXTURE.items() if k != "fetched_at"}
    # What the removed path made of the broker's /api/developer answer.
    old = DeveloperPanel(DeveloperSummary.model_validate_json(json.dumps(answer)), "card")
    store.set(KEY, json.dumps(FIXTURE))

    new = asyncio.run(client.update())

    assert DeveloperSummary.model_validate(new.summary.model_dump(exclude={"fetched_at"})) == old.summary
    assert (new.style, new.has_release) == (old.style, old.has_release)
    old_client = DeveloperClient(REGISTRATION, DeveloperSettings(enabled=True, style="card"))
    old_client.summary = old.summary
    (new_notices,) = client.notice_sources()
    (old_notices,) = old_client.notice_sources()
    assert new_notices.fetch(NOW) == old_notices.fetch(NOW)


def test_a_stored_panel_without_a_github_token_still_has_the_plans(client, store):
    _put(store, ANSWER | {"github": None})

    panel = asyncio.run(client.update())

    assert panel.summary.github is None
    assert len(panel.summary.plans) == 3


def test_nothing_stored_raises_a_lookup_error_so_the_last_panel_stays(client, store):
    with pytest.raises(NoStoredDeveloperError) as raised:
        asyncio.run(client.update())

    assert isinstance(raised.value, LookupError)
    assert client.summary is None


def test_a_store_that_cant_be_read_raises(client, monkeypatch):
    def unreachable(*_):
        raise reader.redis.exceptions.ConnectionError

    monkeypatch.setattr(reader, "read", unreachable)

    with pytest.raises(reader.redis.exceptions.ConnectionError):
        asyncio.run(client.update())


def test_it_reads_the_store_on_every_cycle(client):
    assert client.poll_interval == dt.timedelta(0)


def test_without_a_store_it_cant_be_built(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)

    with pytest.raises(reader.StoreNotConfiguredError):
        DeveloperClient(REGISTRATION, DeveloperSettings(enabled=True, style="card"))


def test_without_a_device_id_it_cant_be_built():
    with pytest.raises(ValueError, match="device id"):
        DeveloperClient(
            Registration("https://broker.example.com", "shh"), DeveloperSettings(enabled=True, style="card")
        )


@responses.activate  # nothing registered: the notice board's calls fail at once
def test_a_renderer_without_a_store_shows_the_developer_panel_as_failing(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(make_config(developer={"enabled": True})))

    failed = registry.clients["developer"]
    assert isinstance(failed, FailedClient)
    assert isinstance(failed.error, reader.StoreNotConfiguredError)
    assert asyncio.run(registry.update_all())["developer"].message == "Could not connect"


def test_no_notices_before_the_first_answer(client):
    (source,) = client.notice_sources()

    assert source.fetch(NOW) == []


def test_notices_for_failing_builds_renewals_and_a_plan_nearly_used_up(client, store):
    _put(store, ANSWER)
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


def _release(state="running", jobs=None, **fields):
    return {
        "repo": "auth-broker",
        "title": "v1.27.0",
        "state": state,
        "started": "2026-10-17T10:56:00Z",
        "finished": None,
        "jobs": jobs
        or [
            {"key": "tag", "name": "tag", "state": "done"},
            {"key": "deploy", "name": "deploy-authentik", "state": "approve", "needs": ["tag"]},
        ],
    } | fields


def _with_releases(*releases):
    return ANSWER | {"github": ANSWER["github"] | {"releases": list(releases)}}


def test_a_running_release_shows_its_jobs_until_the_provider_says_it_ended(client, store):
    _put(store, _with_releases(_release()))
    panel = asyncio.run(client.update())
    _put(store, _with_releases(_release("done", finished="2026-10-17T11:05:00Z")))
    done = asyncio.run(client.update())

    assert panel.has_release
    (release,) = panel.summary.releases
    assert release.jobs[1].state == "approve"
    assert release.minutes(NOW) == 4
    assert done.summary.releases[0].state == "done"
    # The pace is the provider's to set (the pace key), not the client's.
    assert client.poll_interval == dt.timedelta(0)


def test_a_stored_panel_without_releases_has_none(client, store):
    _put(store, ANSWER)

    panel = asyncio.run(client.update())

    assert panel.summary.releases == []
    assert not panel.has_release


def test_notices_for_a_release_failed_and_one_waiting_for_approval(client, store):
    failed = _release(
        "failed", title="v2.14.0", repo="pi-dashboard", jobs=[{"key": "b", "name": "b", "state": "failed"}]
    )
    _put(store, _with_releases(_release(), failed))
    asyncio.run(client.update())
    (source,) = client.notice_sources()

    notices = [(n.severity, n.text) for n in source.fetch(NOW) if n.source == "GitHub"]

    assert notices == [
        (Severity.WARNING, "auth-broker: build failing"),
        (Severity.REMINDER, "auth-broker v1.27.0: deploy-authentik waiting for your approval"),
        (Severity.WARNING, "pi-dashboard v2.14.0: release failed"),
    ]


def test_a_finished_release_counts_the_minutes_it_ran_for():
    release = Release.model_validate(_release("done", finished="2026-10-17T11:05:30Z"))

    assert release.minutes(NOW + dt.timedelta(hours=1)) == 10
    assert release.waiting == ["deploy-authentik"]
