"""The render pool's metrics: every one per screen, by its `device` attribute (the slug), sent as OTLP to Fluent Bit.

docs/stateless-renderers.md, "Observability". A worker's resource is the worker (`host.name` is its WORKER_NAME), not a
screen, so each screen is the `device` attribute, and each render's outcome, where there is one, is `outcome`:

- `countdown.render.duration` (histogram, s): how long each render took, by `device` and `outcome`.
- `countdown.render.count` (counter): renders by `device` and `outcome`, which is `written`, `unchanged` (skipped:
  nothing changed) or `failed`.
- `countdown.render.last_success` (gauge, Unix s): when the screen last got a good frame (`written` or `unchanged`).
- `countdown.render.expired_panels` (gauge): by `device` and `panel`, 1 if that panel was drawn "Not available" because
  its key had expired, else 0.
- `countdown.render.lag` (histogram, s): how long each job waited in the queue, by `device`.
- The counter and the histograms also have `worker`, the worker's name (its `host.name`).
- `countdown.render.queued` (gauge): how many jobs are waiting (the queue's, so no `device`).
- `countdown.api.status` (gauge): 1 per API, by `device`, `api` and `status` (each client's status as of the screen's
  latest render; an API with no client is `disabled`).
- `countdown.problem` (gauge): by `device`, 1 while the latest render failed or any API is in `error` or `fatal`.
- `countdown.stage` (gauge): 1, by `device` and `stage` (`pairing` or `running`).

Two workers report side by side, each its own counters and histograms, so sum those across workers. Those also carry
the worker as a `worker` attribute: the host's Fluent Bit drops the resource's attributes (`host.name` among them) on
the way to VictoriaMetrics, and without it the two workers' counters would land in one series there and read as a
reset at every sample. `last_success` is each worker's latest for every screen still in the pool: take the highest
across workers.
The other gauges describe the screen as its latest render left it, so each render's are reported once, at the next
export, by the worker that drew it; a screen that hasn't been drawn since the last export has none from that worker.

A render that fails counts as `failed`, leaves `last_success` where it was, and reports `countdown.problem` 1. That
includes a frame that was drawn but couldn't be written (render.Result.UNRECORDED) and a screen with no config in the
store (render.Result.NO_CONFIG): neither gave the screen a frame.
"""

import logging
import threading
import time
from typing import TYPE_CHECKING

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import HOST_NAME, SERVICE_INSTANCE_ID, SERVICE_NAME, Resource
from redis.exceptions import RedisError

from countdown_core.core.abstract_client import ClientStatus
from countdown_core.core.api_registry import API_NAMES
from countdown_core.render import Outcome, Result

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

# The host's Fluent Bit OpenTelemetry input (OTLP/HTTP), on the host's network.
METRICS_URL = "http://127.0.0.1:4318/v1/metrics"
# Once a minute.
EXPORT_INTERVAL_MS = 60_000
# Per export, retries included. With no collector listening an export retries until this is up, and an export in
# flight holds up the worker's exit, which a container only gets 10s for.
EXPORT_TIMEOUT_MS = 3_000
# A client in either of these makes the screen's countdown.problem 1.
UNHEALTHY = frozenset({ClientStatus.ERROR, ClientStatus.FATAL})

# What countdown.render.count's `outcome` is: the frame written, or skipped because the record holds it, or neither.
WRITTEN = "written"
UNCHANGED = "unchanged"
FAILED = "failed"
GOOD = {Result.WRITTEN: WRITTEN, Result.UNCHANGED: UNCHANGED}

# Seconds. A warm render takes about 50 ms; the deadline is 20 s.
DURATION_BUCKETS = (0.025, 0.05, 0.075, 0.1, 0.15, 0.25, 0.5, 1, 2.5, 5, 10, 20)
# Seconds a job waits: the frames due on the minute should land within LEAD_S (3 s) of it.
LAG_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60)


def outcome_of(outcome: Outcome | None) -> str:
    """Return countdown.render.count's `outcome` for a render's Outcome; None (the render raised) is `failed`."""
    if outcome is None:
        return FAILED
    return GOOD.get(outcome.result, FAILED)


class PoolMetrics:
    """The render pool's metrics, for one worker process. Thread-safe: the SDK's export thread reads what renders set.

    `screens` and `queued` read the store (the pool's screens, and how many jobs wait); either may raise RedisError,
    and its gauge is then left out of that export. `reader` is for tests; by default metrics go to METRICS_URL.
    """

    def __init__(
        self,
        worker_name: str,
        *,
        screens: Callable[[], Iterable[str]],
        queued: Callable[[], int],
        reader: MetricReader | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Report as the worker `worker_name`, with the pool's `screens` (device ids) and `queued` from the store."""
        self.worker_name = worker_name
        self._screens = screens
        self._queued = queued
        self._reader = reader
        self._clock = clock
        self._lock = threading.Lock()
        # Every screen's latest good frame from this worker, by device id: (slug, Unix time).
        self._last_success: dict[str, tuple[str, float]] = {}
        # What each screen's latest render left it at, by slug, until the next export reports it.
        self._expired: dict[str, dict[str, int]] = {}
        self._statuses: dict[str, dict[str, ClientStatus]] = {}
        self._problem: dict[str, int] = {}
        self._stage: dict[str, str] = {}
        self._provider: MeterProvider | None = None

    def start(self) -> None:
        """Start exporting. Nothing is exported, and no thread runs, until this is called."""
        reader = self._reader or PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=METRICS_URL, timeout=EXPORT_TIMEOUT_MS // 1000),
            export_interval_millis=EXPORT_INTERVAL_MS,
            export_timeout_millis=EXPORT_TIMEOUT_MS,
        )
        self._provider = MeterProvider(
            resource=Resource.create(
                {SERVICE_NAME: "countdown", HOST_NAME: self.worker_name, SERVICE_INSTANCE_ID: self.worker_name}
            ),
            metric_readers=[reader],
            # No final export at exit: the SDK's would retry against a collector that's down.
            shutdown_on_exit=False,
        )
        meter = self._provider.get_meter("countdown")
        self._duration = meter.create_histogram(
            "countdown.render.duration", "s", explicit_bucket_boundaries_advisory=DURATION_BUCKETS
        )
        self._count = meter.create_counter("countdown.render.count", "1")
        self._lag = meter.create_histogram("countdown.render.lag", "s", explicit_bucket_boundaries_advisory=LAG_BUCKETS)
        meter.create_observable_gauge("countdown.render.last_success", [self._observe_last_success], "s")
        meter.create_observable_gauge("countdown.render.expired_panels", [self._observe_expired], "1")
        meter.create_observable_gauge("countdown.render.queued", [self._observe_queued], "1")
        meter.create_observable_gauge("countdown.api.status", [self._observe_statuses], "1")
        meter.create_observable_gauge("countdown.problem", [self._observe_problem], "1")
        meter.create_observable_gauge("countdown.stage", [self._observe_stage], "1")

    def shutdown(self) -> None:
        """Stop exporting, and the SDK's export thread with it. Does nothing before start()."""
        if self._provider is not None:
            self._provider.shutdown()

    def waited(self, slug: str, seconds: float) -> None:
        """Record how long a job for the screen `slug` waited in the queue."""
        if self._provider is not None:
            self._lag.record(max(seconds, 0.0), {"device": slug, "worker": self.worker_name})

    def rendered(self, device_id: str, slug: str, outcome: Outcome | None, duration_s: float) -> None:
        """Record a render of the screen: its Outcome, or None if it raised, and how long it took."""
        result = outcome_of(outcome)
        if self._provider is not None:
            attributes = {"device": slug, "outcome": result, "worker": self.worker_name}
            self._duration.record(duration_s, attributes)
            self._count.add(1, attributes)
        with self._lock:
            if result != FAILED:
                self._last_success[device_id] = (slug, self._clock())
            drawn = outcome is not None and outcome.stage is not None
            if drawn:
                self._expired[slug] = {name: int(name in outcome.expired) for name in outcome.statuses}
                self._statuses[slug] = dict(outcome.statuses)
                self._stage[slug] = outcome.stage.value
            unhealthy = drawn and any(status in UNHEALTHY for status in outcome.statuses.values())
            self._problem[slug] = int(result == FAILED or unhealthy)

    def _observe_last_success(self, _options: CallbackOptions) -> Iterable[Observation]:
        try:
            listed = set(self._screens())
        except RedisError as e:
            logger.debug("Couldn't read the pool's screens for the metrics (%s)", type(e).__name__)
            return []
        with self._lock:
            # A screen no longer in the pool (forgotten, or unpaired) isn't this worker's to report on.
            for device_id in self._last_success.keys() - listed:
                del self._last_success[device_id]
            return [Observation(at, {"device": slug}) for slug, at in self._last_success.values()]

    def _observe_queued(self, _options: CallbackOptions) -> Iterable[Observation]:
        try:
            return [Observation(self._queued())]
        except RedisError as e:
            logger.debug("Couldn't count the queued jobs for the metrics (%s)", type(e).__name__)
            return []

    def _drain[V](self, latest: dict[str, V]) -> list[tuple[str, V]]:
        """Return what renders have set since the last export, and forget it: each render is reported once."""
        with self._lock:
            drained = list(latest.items())
            latest.clear()
        return drained

    def _observe_expired(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [
            Observation(expired, {"device": slug, "panel": panel})
            for slug, panels in self._drain(self._expired)
            for panel, expired in panels.items()
        ]

    def _observe_statuses(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [
            Observation(1, {"device": slug, "api": name, "status": statuses.get(name, ClientStatus.DISABLED).value})
            for slug, statuses in self._drain(self._statuses)
            for name in API_NAMES
        ]

    def _observe_problem(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(problem, {"device": slug}) for slug, problem in self._drain(self._problem)]

    def _observe_stage(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(1, {"device": slug, "stage": stage}) for slug, stage in self._drain(self._stage)]
