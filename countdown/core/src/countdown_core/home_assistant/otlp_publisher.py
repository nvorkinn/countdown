"""The device's health, sent as OpenTelemetry metrics to the local Fluent Bit, which passes it to Home Assistant."""

import asyncio
import logging
from typing import TYPE_CHECKING

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import HOST_NAME, SERVICE_NAME, Resource

from countdown_core.core.abstract_client import AbstractClient, ClientStatus
from countdown_core.home_assistant.device_status import DeviceStatus
from countdown_credentials.device_name import device_name_from_env

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

# The local Fluent Bit's OpenTelemetry input (OTLP/HTTP).
METRICS_URL = "http://127.0.0.1:4318/v1/metrics"

# Once a minute.
EXPORT_INTERVAL_MS = 60_000
# Per export, retries included. With no collector listening an export retries until this is up, and
# an export in flight holds up the app's exit, which a container only gets 10s for.
EXPORT_TIMEOUT_MS = 3_000
UNHEALTHY = frozenset({ClientStatus.ERROR, ClientStatus.FATAL})


class OtlpPublisher:
    """Reports the API clients' health and the device's stage as OTLP metrics to the local Fluent Bit.

    It runs from boot, while the device is still registering. The gauges read the live state each time the SDK
    exports (every EXPORT_INTERVAL_MS, or on publish_soon()). `clients` is empty until ApiRegistry points it at its
    own dict, so rebuilt clients are picked up automatically.

    `reader` is for tests; by default metrics go to METRICS_URL.
    """

    def __init__(
        self,
        api_names: Sequence[str],
        device_name: str = "",
        status: DeviceStatus | None = None,
        reader: MetricReader | None = None,
    ) -> None:
        """`api_names` are every API reported on, set up or not; `status` is the device's, shared with the app."""
        self.clients: Mapping[str, AbstractClient] = {}
        self.device_status = status or DeviceStatus()
        self.api_names = list(api_names)
        self.device_name = device_name
        self._reader = reader
        self._provider: MeterProvider | None = None
        self._tasks: set[asyncio.Task] = set()

    @classmethod
    def from_env(cls, api_names: Sequence[str], status: DeviceStatus | None = None) -> OtlpPublisher:
        """Make one for the device named by DEVICE_NAME, or else by its hostname."""
        return cls(api_names, device_name=device_name_from_env(), status=status)

    def start(self) -> None:
        """Start exporting, from inside the event loop.

        The first export is now rather than a minute from now, so a device still waiting on the broker shows up at
        once; the SDK takes it from there. Nothing is exported, and no thread runs, until this is called.
        """
        reader = self._reader or PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=METRICS_URL, timeout=EXPORT_TIMEOUT_MS // 1000),
            export_interval_millis=EXPORT_INTERVAL_MS,
            export_timeout_millis=EXPORT_TIMEOUT_MS,
        )
        self._provider = MeterProvider(
            resource=Resource.create({SERVICE_NAME: "countdown", HOST_NAME: self.device_name}),
            metric_readers=[reader],
            # No final export at exit: the SDK's would retry against a collector that's down.
            shutdown_on_exit=False,
        )
        meter = self._provider.get_meter("countdown")
        meter.create_observable_gauge("countdown.api.status", [self._api_status], "1")
        meter.create_observable_gauge("countdown.problem", [self._problem], "1")
        meter.create_observable_gauge("countdown.stage", [self._stage], "1")
        meter.create_observable_gauge("countdown.display.connected", [self._display_connected], "1")
        meter.create_observable_gauge("countdown.broker.last_sync", [self._last_sync], "s")
        self.publish_soon()

    def publish_soon(self) -> None:
        """Export now rather than at the next interval, e.g. for a new stage.

        Call it from inside the event loop; the export itself runs in a thread. Does nothing before start().
        """
        if self._provider is None:
            return
        task = asyncio.create_task(asyncio.to_thread(self._flush))
        self._tasks.add(task)  # asyncio itself only references tasks weakly
        task.add_done_callback(self._tasks.discard)

    def _flush(self) -> None:
        """Never raised: telemetry must not cost the display anything. The SDK logs a failed export."""
        try:
            self._provider.force_flush()
        except Exception as e:  # noqa: BLE001 -- telemetry must not cost the display anything
            logger.warning("OTLP: couldn't publish health: %s", e)

    def shutdown(self) -> None:
        """Stop exporting, and the SDK's export thread with it. Does nothing before start()."""
        if self._provider is not None:
            self._provider.shutdown()

    def _statuses(self) -> dict[str, ClientStatus]:
        return {
            name: self.clients[name].status if name in self.clients else ClientStatus.DISABLED
            for name in self.api_names
        }

    def _api_status(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(1, {"api": n, "status": s.value}) for n, s in self._statuses().items()]

    def _problem(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(int(any(s in UNHEALTHY for s in self._statuses().values())))]

    def _stage(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(1, {"stage": self.device_status.stage})]

    def _display_connected(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(int(self.device_status.display_connected))]

    def _last_sync(self, _options: CallbackOptions) -> Iterable[Observation]:
        synced = self.device_status.last_broker_sync
        return [Observation(synced.timestamp())] if synced else []
