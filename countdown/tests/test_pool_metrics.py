"""The render pool's metrics (countdown_server.pool_metrics): every one by the screen's `device` attribute."""

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from redis.exceptions import ConnectionError as RedisConnectionError

from countdown_core.core.abstract_client import ClientStatus
from countdown_core.core.api_registry import API_NAMES
from countdown_core.render import Outcome, Result, Stage
from countdown_server.pool_metrics import FAILED, UNCHANGED, WRITTEN, PoolMetrics, outcome_of

DEVICE, SLUG = "a1b2c3d4e5f6", "nvorkinn-test"


class Store:
    """The pool's screens and queue length, as the metrics read them from the store."""

    def __init__(self):
        self.screens = {DEVICE}
        self.queued = 3
        self.down = False

    def read_screens(self):
        if self.down:
            raise RedisConnectionError
        return self.screens

    def read_queued(self):
        if self.down:
            raise RedisConnectionError
        return self.queued


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def reader():
    return InMemoryMetricReader()


@pytest.fixture
def metrics(reader, store, clock):
    pool = PoolMetrics(
        "render-worker-1", screens=store.read_screens, queued=store.read_queued, reader=reader, clock=clock
    )
    pool.start()
    yield pool
    pool.shutdown()


def collect(reader) -> dict[str, list[dict]]:
    """Return the data points by metric name, each as {attributes..., "value": ...} (a histogram's with its count)."""
    data = reader.get_metrics_data()
    if data is None or not data.resource_metrics:
        return {}
    points = {}
    for scope in data.resource_metrics[0].scope_metrics:
        for metric in scope.metrics:
            points[metric.name] = [
                {**dict(p.attributes), "value": getattr(p, "value", None), "count": getattr(p, "count", None)}
                for p in metric.data.data_points
            ]
    return points


def drawn(result=Result.WRITTEN, *, expired=frozenset(), statuses=None, stage=Stage.RUNNING) -> Outcome:
    statuses = {"tfl": ClientStatus.CONNECTED, "weather": ClientStatus.CONNECTED} if statuses is None else statuses
    return Outcome(result, stage, statuses=statuses, expired=frozenset(expired))


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (drawn(Result.WRITTEN), WRITTEN),
        (drawn(Result.UNCHANGED), UNCHANGED),
        (drawn(Result.UNRECORDED), FAILED),
        (Outcome(Result.NO_CONFIG), FAILED),
        (None, FAILED),
    ],
)
def test_the_outcome_is_written_unchanged_or_failed(outcome, expected):
    assert outcome_of(outcome) == expected


def test_the_resource_is_the_worker_and_each_render_is_counted_by_device_and_outcome(metrics, reader):
    metrics.rendered(DEVICE, SLUG, drawn(Result.WRITTEN), 0.05)
    metrics.rendered(DEVICE, SLUG, drawn(Result.UNCHANGED), 0.04)
    metrics.rendered(DEVICE, SLUG, drawn(Result.UNCHANGED), 0.04)
    metrics.rendered(DEVICE, SLUG, None, 0.5)

    points = collect(reader)

    counts = {p["outcome"]: p["value"] for p in points["countdown.render.count"]}
    assert counts == {WRITTEN: 1, UNCHANGED: 2, FAILED: 1}
    assert all(p["device"] == SLUG for p in points["countdown.render.count"])
    durations = {p["outcome"]: p["count"] for p in points["countdown.render.duration"]}
    assert durations == {WRITTEN: 1, UNCHANGED: 2, FAILED: 1}
    resource = dict(reader.get_metrics_data().resource_metrics[0].resource.attributes)
    assert resource["host.name"] == "render-worker-1"
    assert resource["service.name"] == "countdown"


def test_last_success_is_the_last_good_frame_and_a_failure_leaves_it(metrics, reader, clock):
    metrics.rendered(DEVICE, SLUG, drawn(Result.UNCHANGED), 0.05)
    good = clock.now
    clock.now += 60
    metrics.rendered(DEVICE, SLUG, None, 0.05)
    clock.now += 60
    metrics.rendered(DEVICE, SLUG, drawn(Result.UNRECORDED), 0.05)

    assert collect(reader)["countdown.render.last_success"] == [{"device": SLUG, "value": good, "count": None}]


def test_last_success_is_reported_every_export_but_only_for_screens_still_in_the_pool(metrics, reader, store):
    metrics.rendered(DEVICE, SLUG, drawn(), 0.05)
    assert len(collect(reader)["countdown.render.last_success"]) == 1
    assert len(collect(reader)["countdown.render.last_success"]) == 1

    store.screens = set()  # rolled back to its own renderer

    assert "countdown.render.last_success" not in collect(reader)


def test_last_success_is_left_out_while_the_store_cant_say_which_screens_are_pooled(metrics, reader, store):
    metrics.rendered(DEVICE, SLUG, drawn(), 0.05)
    store.down = True

    points = collect(reader)

    assert "countdown.render.last_success" not in points
    assert "countdown.render.queued" not in points


def test_expired_panels_are_one_per_panel_and_reported_once(metrics, reader):
    metrics.rendered(DEVICE, SLUG, drawn(expired={"weather"}), 0.05)

    first = collect(reader)["countdown.render.expired_panels"]
    second = collect(reader)

    assert sorted((p["panel"], p["value"]) for p in first) == [("tfl", 0), ("weather", 1)]
    assert all(p["device"] == SLUG for p in first)
    assert "countdown.render.expired_panels" not in second  # not drawn since: no stale value from this worker


def test_the_screens_gauges_move_to_the_device_attribute(metrics, reader):
    metrics.rendered(DEVICE, SLUG, drawn(statuses={"tfl": ClientStatus.ERROR}), 0.05)

    points = collect(reader)

    statuses = {p["api"]: p["status"] for p in points["countdown.api.status"]}
    assert statuses == {name: "error" if name == "tfl" else "disabled" for name in API_NAMES}
    assert all(p["device"] == SLUG for p in points["countdown.api.status"])
    assert points["countdown.problem"] == [{"device": SLUG, "value": 1, "count": None}]
    assert points["countdown.stage"] == [{"device": SLUG, "stage": "running", "value": 1, "count": None}]


def test_a_failed_render_is_a_problem_with_no_stage(metrics, reader):
    metrics.rendered(DEVICE, SLUG, None, 0.05)

    points = collect(reader)

    assert points["countdown.problem"] == [{"device": SLUG, "value": 1, "count": None}]
    assert "countdown.stage" not in points
    assert "countdown.render.expired_panels" not in points


def test_a_healthy_render_is_no_problem(metrics, reader):
    metrics.rendered(DEVICE, SLUG, drawn(), 0.05)

    assert collect(reader)["countdown.problem"] == [{"device": SLUG, "value": 0, "count": None}]


def test_the_wait_and_the_queue_are_the_pools_capacity_signals(metrics, reader):
    metrics.waited(SLUG, 0.2)
    metrics.waited(SLUG, 1.5)

    points = collect(reader)

    assert points["countdown.render.lag"] == [{"device": SLUG, "value": None, "count": 2}]
    assert points["countdown.render.queued"] == [{"value": 3, "count": None}]


def test_nothing_is_recorded_before_start(store, clock):
    pool = PoolMetrics("w", screens=store.read_screens, queued=store.read_queued, clock=clock)

    pool.waited(SLUG, 1)
    pool.rendered(DEVICE, SLUG, drawn(), 0.05)  # no SDK yet: only the state, nothing raises
    pool.shutdown()
