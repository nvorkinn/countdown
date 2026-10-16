import asyncio
import logging
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from test_utils import REGISTRATION

from countdown_core.core.abstract_client import AbstractClient, ClientStatus
from countdown_core.home_assistant import otlp_publisher
from countdown_core.home_assistant.device_status import DeviceStatus
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_credentials import device_name

API_NAMES = ["glowmarkt", "tfl"]
NOW = 1_700_000_000_000_000_000


class StubClient(AbstractClient):
    def __init__(self, status: ClientStatus):
        super().__init__(REGISTRATION)
        self.status = status

    def _initialise(self) -> None:
        pass

    def _update(self):
        pass


def started(pub: OtlpPublisher) -> OtlpPublisher:
    asyncio.run(_start(pub))
    return pub


async def _start(pub: OtlpPublisher) -> None:
    pub.start()
    await asyncio.gather(*pub._tasks)


@pytest.fixture
def reader():
    return InMemoryMetricReader()


def publisher(reader, clients=None, **kwargs) -> OtlpPublisher:
    pub = OtlpPublisher(API_NAMES, device_name="sister-hat", reader=reader, **kwargs)
    if clients is not None:
        pub.clients = clients
    return started(pub)


def metrics(reader) -> dict[str, list[dict]]:
    """Get the latest data points by metric name, each as {attributes..., "value": ...}."""
    data = reader.get_metrics_data()
    [resource_metrics] = data.resource_metrics
    [scope_metrics] = resource_metrics.scope_metrics
    return {
        m.name: [{**dict(p.attributes), "value": p.value} for p in m.data.data_points] for m in scope_metrics.metrics
    }


def resource_attributes(reader) -> dict:
    return dict(reader.get_metrics_data().resource_metrics[0].resource.attributes)


# --- the metrics ---------------------------------------------------------------------


def test_the_resource_names_the_service_and_device(reader):
    publisher(reader)

    attributes = resource_attributes(reader)
    assert attributes["service.name"] == "countdown"
    assert attributes["host.name"] == "sister-hat"


def test_before_there_are_any_clients_every_api_reads_as_disabled(reader):
    pub = publisher(reader)

    assert pub.clients == {}
    assert metrics(reader)["countdown.api.status"] == [
        {"api": "glowmarkt", "status": "disabled", "value": 1},
        {"api": "tfl", "status": "disabled", "value": 1},
    ]
    assert metrics(reader)["countdown.problem"] == [{"value": 0}]


def test_each_clients_status_is_reported(reader):
    publisher(reader, {"glowmarkt": StubClient(ClientStatus.CONNECTED), "tfl": StubClient(ClientStatus.UNINITIALISED)})

    assert metrics(reader)["countdown.api.status"] == [
        {"api": "glowmarkt", "status": "connected", "value": 1},
        {"api": "tfl", "status": "uninitialised", "value": 1},
    ]


def test_an_api_with_no_client_reads_as_disabled(reader):
    publisher(reader, {"tfl": StubClient(ClientStatus.CONNECTED)})

    assert metrics(reader)["countdown.api.status"][0] == {"api": "glowmarkt", "status": "disabled", "value": 1}


@pytest.mark.parametrize("status", [ClientStatus.ERROR, ClientStatus.FATAL])
def test_problem_is_set_while_any_api_is_in_error(reader, status):
    publisher(reader, {"glowmarkt": StubClient(ClientStatus.CONNECTED), "tfl": StubClient(status)})

    assert metrics(reader)["countdown.problem"] == [{"value": 1}]


def test_metrics_follow_the_clients_as_they_change(reader):
    tfl = StubClient(ClientStatus.CONNECTED)
    publisher(reader, {"tfl": tfl})
    assert metrics(reader)["countdown.problem"] == [{"value": 0}]

    tfl.status = ClientStatus.ERROR

    assert metrics(reader)["countdown.problem"] == [{"value": 1}]


def test_the_devices_stage_display_and_last_sync_are_reported(reader):
    synced = datetime(2026, 9, 20, 10, 30, tzinfo=UTC)
    status = DeviceStatus(stage="setup", last_broker_sync=synced, display=SimpleNamespace(panel_connected=True))
    publisher(reader, status=status)

    out = metrics(reader)

    assert out["countdown.stage"] == [{"stage": "setup", "value": 1}]
    assert out["countdown.display.connected"] == [{"value": 1}]
    assert out["countdown.broker.last_sync"] == [{"value": synced.timestamp()}]


def test_before_the_first_broker_sync_there_is_no_last_sync_metric(reader):
    publisher(reader)

    out = metrics(reader)

    assert out["countdown.stage"] == [{"stage": "waiting_for_broker", "value": 1}]
    assert "countdown.broker.last_sync" not in out


@pytest.mark.parametrize("panel_connected", [None, False])
def test_a_display_that_is_unknown_or_absent_is_not_reported_as_connected(reader, panel_connected):
    publisher(reader, status=DeviceStatus(display=SimpleNamespace(panel_connected=panel_connected)))

    assert metrics(reader)["countdown.display.connected"] == [{"value": 0}]


def test_with_no_display_at_all_it_is_not_connected():
    assert DeviceStatus().display_connected is False


# --- exporting -----------------------------------------------------------------------


def test_nothing_is_exported_before_start():
    pub = OtlpPublisher(API_NAMES, device_name="sister-hat")

    pub.publish_soon()  # outside any event loop: must be a no-op

    assert pub._provider is None


def test_start_exports_at_once_and_publish_soon_again(monkeypatch):
    flushes = []

    async def scenario():
        pub = OtlpPublisher(API_NAMES, device_name="sister-hat", reader=InMemoryMetricReader())
        monkeypatch.setattr(pub, "_flush", lambda: flushes.append(1))
        pub.start()
        await asyncio.gather(*pub._tasks)
        pub.publish_soon()
        await asyncio.gather(*pub._tasks)

    asyncio.run(scenario())

    assert len(flushes) == 2


def test_a_failed_export_is_logged_not_raised(caplog):
    async def scenario():
        pub = OtlpPublisher(API_NAMES, device_name="sister-hat", reader=InMemoryMetricReader())
        pub.start()
        await asyncio.gather(*pub._tasks)
        pub._provider.force_flush = lambda *a: (_ for _ in ()).throw(OSError("refused"))
        with caplog.at_level(logging.WARNING):
            pub.publish_soon()
            await asyncio.gather(*pub._tasks)

    asyncio.run(scenario())

    assert "refused" in caplog.text


def test_by_default_it_exports_protobuf_over_http_to_the_collector(monkeypatch):
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (self.path, self.headers["Content-Type"], self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(otlp_publisher, "METRICS_URL", f"http://127.0.0.1:{server.server_port}/v1/metrics")

    async def scenario():
        pub = OtlpPublisher(API_NAMES, device_name="sister-hat")
        pub.start()
        await asyncio.gather(*pub._tasks)
        pub.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()

    path, content_type, body = received[0]
    request = ExportMetricsServiceRequest.FromString(body)
    names = {m.name for m in request.resource_metrics[0].scope_metrics[0].metrics}
    assert path == "/v1/metrics"
    assert content_type == "application/x-protobuf"
    assert "countdown.api.status" in names


def test_exiting_is_not_held_up_by_a_collector_that_is_down():
    """A container gets 10s to stop.

    Nothing listens on the port here (it's bound and closed to get one that's free), so an export in flight retries
    until its timeout.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = f"""
import asyncio, sys
from countdown_core.home_assistant import otlp_publisher
otlp_publisher.METRICS_URL = "http://127.0.0.1:{port}/v1/metrics"

async def main():
    pub = otlp_publisher.OtlpPublisher(["tfl"], device_name="x")
    pub.start()
    await asyncio.sleep(0.5)  # the first export is still retrying
    sys.exit(0)

asyncio.run(main())
"""
    started = time.monotonic()
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)  # noqa: S603 -- our own script

    assert time.monotonic() - started < otlp_publisher.EXPORT_TIMEOUT_MS / 1000 + 2


# --- from the environment ------------------------------------------------------------


def test_from_env_uses_the_device_name(monkeypatch):
    monkeypatch.setenv("DEVICE_NAME", "Sister Hat")

    pub = OtlpPublisher.from_env(API_NAMES)

    assert pub.device_name == "sister-hat"


def test_from_env_falls_back_to_the_hostname(monkeypatch):
    monkeypatch.setattr(device_name.socket, "gethostname", lambda: "Pi-Zero")

    assert OtlpPublisher.from_env(API_NAMES).device_name == "pi-zero"
