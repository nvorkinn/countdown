"""The developer panel's client: it reads what auth-broker's developer provider stored, and never asks the broker.

The store contract's developer.json (countdown_api.fixtures) is the stored value, which auth-broker's tests read too.
"""

import asyncio
import datetime as dt
import json

import pytest
import responses
from config_factory import make_config
from test_utils import TEST_DEVICE_ID

from countdown_api import fixtures
from countdown_core.core.api_registry import ApiRegistry, FailedClient
from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.models import AiPlan, DeveloperSummary, Release
from countdown_core.notices.notice import Severity
from countdown_core.store import keys, reader
from countdown_core.store.developer import NoStoredDeveloperError, StoredDeveloper

FIXTURE = fixtures.load("developer.json")
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
    """Store `answer` (the panel's `github` and `plans`) as the developer provider would, with `fetched_at`."""
    store.set(KEY, json.dumps({"fetched_at": "2026-10-17T10:59:00+00:00", **answer}))


@pytest.fixture
def client() -> DeveloperClient:
    return DeveloperClient(TEST_DEVICE_ID, DeveloperSettings(enabled=True, style="card"))


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
    """The fixture without `fetched_at` is the broker's /api/developer answer: the stored panel must match it."""
    answer = {k: v for k, v in FIXTURE.items() if k != "fetched_at"}
    # The summary and panel drawn from the broker's /api/developer answer, which the stored copy must match.
    old = DeveloperPanel(DeveloperSummary.model_validate_json(json.dumps(answer)), "card")
    store.set(KEY, json.dumps(FIXTURE))

    new = asyncio.run(client.update())

    assert DeveloperSummary.model_validate(new.summary.model_dump(exclude={"fetched_at"})) == old.summary
    assert (new.style, new.has_release) == (old.style, old.has_release)


def test_a_stored_panel_without_a_github_token_still_has_the_plans(client, store):
    _put(store, ANSWER | {"github": None})

    panel = asyncio.run(client.update())

    assert panel.summary.github is None
    assert len(panel.summary.plans) == 3


def test_nothing_stored_says_the_developer_panel_is_not_available(client, store):
    """The key has expired (the developer provider is down): nothing older than its TTL is drawn."""
    with pytest.raises(NoStoredDeveloperError) as raised:
        client._update()
    panel = asyncio.run(client.update())

    assert isinstance(raised.value, reader.NoStoredDataError)
    assert (panel.title, panel.message) == ("Developer", "Not available")


def test_an_expired_key_is_not_available_on_the_next_poll(client, store):
    _put(store, ANSWER)
    assert isinstance(asyncio.run(client.update()), DeveloperPanel)

    store.delete(KEY)

    assert asyncio.run(client.update()).message == "Not available"


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
        DeveloperClient(TEST_DEVICE_ID, DeveloperSettings(enabled=True, style="card"))


@responses.activate  # nothing registered: the notice board's calls fail at once
def test_a_renderer_without_a_store_shows_the_developer_panel_as_failing(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    registry = ApiRegistry(TEST_DEVICE_ID, make_config(developer={"enabled": True}))

    failed = registry.clients["developer"]
    assert isinstance(failed, FailedClient)
    assert isinstance(failed.error, reader.StoreNotConfiguredError)
    assert registry.panels(registry.poll())["developer"].message == "Could not connect"


def test_the_notice_source_raises_while_nothing_is_stored_so_the_board_leaves_it_out(client):
    (source,) = client.notice_sources()

    with pytest.raises(NoStoredDeveloperError):
        source.fetch(NOW)


def test_notices_for_failing_builds_renewals_and_a_plan_nearly_used_up(client, store):
    _put(store, ANSWER)
    (source,) = client.notice_sources()

    notices = [(n.source, n.severity, n.text) for n in source.fetch(NOW)]

    assert notices == [
        ("GitHub", Severity.WARNING, "auth-broker: build failing"),
        ("Copilot", Severity.INFO, "93% of Pro used"),
        ("Claude", Severity.REMINDER, "Max renews tomorrow"),
        ("Gemini", Severity.REMINDER, "AI Pro renews today"),
    ]


def test_the_notice_source_reads_the_store_itself_not_the_panels_last_poll(client, store):
    """It reads the store, not the client's last answer: the board draws this cycle's notices, not the last one's."""
    _put(store, ANSWER)
    asyncio.run(client.update())  # the panel's poll, before the board's
    (source,) = client.notice_sources()
    _put(store, ANSWER | {"github": ANSWER["github"] | {"failing": ["pi-dashboard"]}})  # the provider's next cycle

    texts = [n.text for n in source.fetch(NOW) if n.source == "GitHub"]
    store.delete(KEY)  # then its key expires

    assert texts == ["pi-dashboard: build failing"]
    with pytest.raises(NoStoredDeveloperError):
        source.fetch(NOW)


def test_the_notice_source_reads_the_store_so_the_board_counts_it_as_one_that_does(client):
    """Like the board's other stored sources: if all of theirs have expired, the board says it's not available."""
    (source,) = client.notice_sources()

    assert source.needs_network is True


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


def test_the_shared_fixture_is_a_stored_developer():
    stored = StoredDeveloper.model_validate(FIXTURE)

    assert stored.github.login == "nvorkinn"
    assert [release.state for release in stored.releases] == ["running"]
    assert [plan.name for plan in stored.plans] == ["Copilot", "Claude"]
