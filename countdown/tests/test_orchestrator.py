import docker
import pytest
import responses

from countdown_orchestrator.broker import OrchestratorBroker
from countdown_orchestrator.models import Assignment, Status, valid_slug
from countdown_orchestrator.reconcile import reconcile
from countdown_orchestrator.runtime import MANAGED_LABEL, SLUG_LABEL, DockerRuntime, Renderer

BROKER_URL = "https://broker.example.com"
LIST_URL = f"{BROKER_URL}/api/orchestrator/assignments"


def _assignment(slug="lisa", status=Status.PENDING, id_="a1") -> Assignment:
    return Assignment(id=id_, slug=slug, claim_token=f"token-{slug}", status=status)


class FakeBroker:
    def __init__(self, *assignments: Assignment):
        self._assignments = list(assignments)
        self.reports: list[tuple[str, Status, str]] = []

    def assignments(self):
        return self._assignments

    def report(self, assignment, status, detail=""):
        self.reports.append((assignment.slug, status, detail))


class FakeRuntime:
    def __init__(self, *renderers: Renderer):
        self.existing = {r.slug: r for r in renderers}
        self.ensured: list[str] = []
        self.removed: list[str] = []

    def renderers(self):
        return dict(self.existing)

    def ensure(self, assignment):
        self.ensured.append(assignment.slug)
        self.existing[assignment.slug] = Renderer(assignment.slug, True)

    def remove(self, slug):
        self.removed.append(slug)
        self.existing.pop(slug, None)


# --- Slugs --------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["lisa", "tom-2", "a_b", "x1", "9lives"])
def test_valid_slugs(slug):
    assert valid_slug(slug)


@pytest.mark.parametrize("slug", ["", "a", "-lisa", "Lisa", "li sa", "lisa!", "x" * 33, "lisa\n", "../etc"])
def test_invalid_slugs(slug):
    assert not valid_slug(slug)


# --- Reconciling --------------------------------------------------------------------


def test_a_pending_assignment_gets_a_renderer_and_is_reported_provisioned():
    broker, runtime = FakeBroker(_assignment()), FakeRuntime()

    reconcile(broker, runtime)

    assert runtime.ensured == ["lisa"]
    assert broker.reports == [("lisa", Status.PROVISIONED, "")]


def test_a_second_pass_changes_nothing():
    broker, runtime = FakeBroker(_assignment()), FakeRuntime()
    reconcile(broker, runtime)
    broker._assignments = [_assignment(status=Status.PROVISIONED)]
    broker.reports.clear()

    reconcile(broker, runtime)

    assert runtime.ensured == ["lisa"]
    assert broker.reports == []


def test_a_running_renderer_for_a_pending_assignment_is_only_reported():
    broker, runtime = FakeBroker(_assignment()), FakeRuntime(Renderer("lisa", True))

    reconcile(broker, runtime)

    assert runtime.ensured == []
    assert broker.reports == [("lisa", Status.PROVISIONED, "")]


def test_a_provisioned_assignment_whose_renderer_vanished_is_made_again():
    broker, runtime = FakeBroker(_assignment(status=Status.PROVISIONED)), FakeRuntime()

    reconcile(broker, runtime)

    assert runtime.ensured == ["lisa"]


def test_a_stopped_renderer_is_started_again():
    broker = FakeBroker(_assignment(status=Status.PROVISIONED))
    runtime = FakeRuntime(Renderer("lisa", False))

    reconcile(broker, runtime)

    assert runtime.ensured == ["lisa"]


def test_a_deleting_assignment_removes_the_renderer_and_reports_deleted():
    broker = FakeBroker(_assignment(status=Status.DELETING))
    runtime = FakeRuntime(Renderer("lisa", True))

    reconcile(broker, runtime)

    assert runtime.removed == ["lisa"]
    assert broker.reports == [("lisa", Status.DELETED, "")]


def test_an_invalid_slug_is_reported_failed_and_never_run():
    broker, runtime = FakeBroker(_assignment(slug="../etc")), FakeRuntime()

    reconcile(broker, runtime)

    assert runtime.ensured == []
    assert broker.reports[0][:2] == ("../etc", Status.FAILED)


def test_one_failing_assignment_doesnt_stop_the_others():
    class Flaky(FakeRuntime):
        def ensure(self, assignment):
            if assignment.slug == "bad":
                raise RuntimeError("no image")
            super().ensure(assignment)

    broker = FakeBroker(_assignment("bad", id_="1"), _assignment("good", id_="2"))
    runtime = Flaky()

    reconcile(broker, runtime)

    assert runtime.ensured == ["good"]
    assert broker.reports == [
        ("bad", Status.FAILED, "RuntimeError: no image"),
        ("good", Status.PROVISIONED, ""),
    ]


def test_a_failure_already_reported_isnt_reported_again():
    class Broken(FakeRuntime):
        def ensure(self, assignment):
            raise RuntimeError("no image")

    broker = FakeBroker(_assignment(status=Status.FAILED))

    reconcile(broker, Broken())

    assert broker.reports == []


def test_a_broker_error_while_reporting_a_failure_doesnt_escape():
    class Broken(FakeRuntime):
        def ensure(self, assignment):
            raise RuntimeError("no image")

    class DownBroker(FakeBroker):
        def report(self, assignment, status, detail=""):
            raise ConnectionError("down")

    reconcile(DownBroker(_assignment()), Broken())


def test_a_renderer_nobody_wants_is_left_alone():
    broker, runtime = FakeBroker(), FakeRuntime(Renderer("stray", True))

    reconcile(broker, runtime)

    assert runtime.removed == []


def test_nothing_new_is_started_at_capacity():
    broker = FakeBroker(_assignment("new"), _assignment("old", status=Status.PROVISIONED, id_="2"))
    runtime = FakeRuntime(Renderer("old", True))

    reconcile(broker, runtime, max_renderers=1)

    assert runtime.ensured == []
    assert broker.reports == []


def test_capacity_doesnt_stop_an_existing_renderer_being_restarted():
    broker = FakeBroker(_assignment("old", status=Status.PROVISIONED))
    runtime = FakeRuntime(Renderer("old", False))

    reconcile(broker, runtime, max_renderers=1)

    assert runtime.ensured == ["old"]


# --- The broker's API ---------------------------------------------------------------


@responses.activate
def test_the_assignments_are_listed_with_the_orchestrators_own_token():
    responses.get(
        LIST_URL,
        json={"assignments": [{"id": "a1", "slug": "lisa", "claim_token": "t", "status": "pending"}]},
    )

    assert OrchestratorBroker(BROKER_URL + "/", "orch-token").assignments() == [
        Assignment("a1", "lisa", "t", Status.PENDING)
    ]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer orch-token"


@responses.activate
def test_a_report_is_posted_for_the_assignment():
    responses.post(f"{LIST_URL}/a1/status", json={})

    OrchestratorBroker(BROKER_URL, "t").report(_assignment(), Status.FAILED, "boom")

    assert responses.calls[0].request.body == b'{"status": "failed", "detail": "boom"}'


# --- Docker -------------------------------------------------------------------------


class FakeContainer:
    def __init__(self, status="running", slug="lisa"):
        self.status = status
        self.labels = {SLUG_LABEL: slug}
        self.started = False
        self.removed: dict | None = None

    def start(self):
        self.started = True

    def remove(self, **kwargs):
        self.removed = kwargs


class FakeVolumes:
    def __init__(self):
        self.removed: list[str] = []

    def get(self, name):
        volumes = self

        class Volume:
            def remove(self, **_):
                volumes.removed.append(name)

        return Volume()


class FakeDocker:
    def __init__(self, container: FakeContainer | None = None):
        self.container = container
        self.run_calls: list[tuple] = []
        self.list_calls: list[dict] = []
        self.volumes = FakeVolumes()
        outer = self

        class Containers:
            def get(self, name):
                if outer.container is None:
                    raise docker.errors.NotFound(name)
                return outer.container

            def run(self, *args, **kwargs):
                outer.run_calls.append((args, kwargs))

            def list(self, **kwargs):
                outer.list_calls.append(kwargs)
                return [outer.container] if outer.container else []

        self.containers = Containers()


def _runtime(client) -> DockerRuntime:
    return DockerRuntime("ghcr.io/x/countdown-server:1", BROKER_URL, client=client)


def test_a_new_renderer_is_a_container_shaped_like_the_compose_services():
    client = FakeDocker()

    _runtime(client).ensure(_assignment())

    ((args, kwargs),) = client.run_calls
    assert args == ("ghcr.io/x/countdown-server:1",)
    assert kwargs["name"] == "renderer-lisa"
    assert kwargs["network_mode"] == "host"
    assert kwargs["restart_policy"] == {"Name": "unless-stopped"}
    assert kwargs["labels"] == {MANAGED_LABEL: "1", SLUG_LABEL: "lisa"}
    assert kwargs["environment"] == {"BROKER_URL": BROKER_URL, "DEVICE_NAME": "lisa", "CLAIM_TOKEN": "token-lisa"}
    assert set(kwargs["volumes"]) == {"renderer-lisa-data", "renderer-lisa-key"}


def test_an_existing_running_renderer_is_left_alone():
    client = FakeDocker(FakeContainer("running"))

    _runtime(client).ensure(_assignment())

    assert client.run_calls == []
    assert not client.container.started


def test_an_existing_stopped_renderer_is_started():
    client = FakeDocker(FakeContainer("exited"))

    _runtime(client).ensure(_assignment())

    assert client.container.started


def test_only_labelled_containers_are_listed():
    client = FakeDocker(FakeContainer("exited", "lisa"))

    assert _runtime(client).renderers() == {"lisa": Renderer("lisa", False)}
    assert client.list_calls == [{"all": True, "filters": {"label": f"{MANAGED_LABEL}=1"}}]


def test_removing_a_renderer_deletes_its_container_and_both_volumes():
    client = FakeDocker(FakeContainer())

    _runtime(client).remove("lisa")

    assert client.container.removed == {"force": True, "v": True}
    assert client.volumes.removed == ["renderer-lisa-data", "renderer-lisa-key"]


def test_removing_a_renderer_that_is_already_gone_is_fine():
    client = FakeDocker()
    client.volumes.get = lambda name: (_ for _ in ()).throw(docker.errors.NotFound(name))

    _runtime(client).remove("lisa")
