"""The orchestrator's entry point: polls the broker and reconciles the renderers with it, forever."""

import logging
import os
import time

import requests

from countdown_orchestrator.broker import OrchestratorBroker
from countdown_orchestrator.log_format import configure_logging
from countdown_orchestrator.reconcile import reconcile
from countdown_orchestrator.runtime import DockerRuntime

logger = logging.getLogger(__name__)

DEFAULT_POLL_S = 10
MAX_BACKOFF_S = 300


def run() -> None:
    """Run the `countdown-orchestrator` console script, on the renderers' host.

    It polls the broker for screens that need a renderer. BROKER_URL, ORCHESTRATOR_TOKEN and RENDERER_IMAGE are
    required (no defaults: crash loudly rather than talk to, or run, something baked in).
    """
    configure_logging()
    broker_url = os.environ["BROKER_URL"]
    broker = OrchestratorBroker(broker_url, os.environ["ORCHESTRATOR_TOKEN"])
    runtime = DockerRuntime(
        image=os.environ["RENDERER_IMAGE"],
        broker_url=broker_url,
        mem_limit=os.environ.get("RENDERER_MEM_LIMIT", "256m"),
    )
    max_renderers = int(os.environ["MAX_RENDERERS"]) if os.environ.get("MAX_RENDERERS") else None
    poll_s = int(os.environ.get("POLL_INTERVAL_S", DEFAULT_POLL_S))

    delay = poll_s
    while True:
        try:
            reconcile(broker, runtime, max_renderers)
            delay = poll_s
        except requests.RequestException as e:
            delay = min(delay * 2, MAX_BACKOFF_S)
            logger.warning("Couldn't reach the broker (%s); retrying in %ds", e, delay)
        except Exception:
            logger.exception("Reconcile failed")
        time.sleep(delay)


if __name__ == "__main__":
    run()
