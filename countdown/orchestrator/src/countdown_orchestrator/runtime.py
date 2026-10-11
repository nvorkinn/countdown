"""Where renderers run. The orchestrator only knows this interface; DockerRuntime is the one implementation."""

import contextlib
from dataclasses import dataclass
from typing import Protocol

from countdown_orchestrator.models import Assignment

# On every container the orchestrator makes, so it only ever touches (and lists) its own.
MANAGED_LABEL = "countdown.managed"
SLUG_LABEL = "countdown.slug"


@dataclass(frozen=True)
class Renderer:
    slug: str
    running: bool


class Runtime(Protocol):
    def renderers(self) -> dict[str, Renderer]:
        """Every renderer the orchestrator has made, by slug."""
        ...

    def ensure(self, assignment: Assignment) -> None:
        """Makes the renderer if it doesn't exist and starts it if it isn't running. Safe to repeat."""
        ...

    def remove(self, slug: str) -> None:
        """Stops the renderer and deletes it with its volumes (its identity and the key to it)."""
        ...


class DockerRuntime:
    """One container per renderer, from the countdown-server image, shaped like the services in
    server/deploy/docker-compose.yml: host networking (for the host's Fluent Bit) and a volume each for the
    identity and its key."""

    def __init__(
        self,
        image: str,
        broker_url: str,
        mem_limit: str = "256m",
        nano_cpus: int = 500_000_000,
        client=None,
    ):
        if client is None:
            import docker

            client = docker.from_env()
        self.client = client
        self.image = image
        self.broker_url = broker_url
        self.mem_limit = mem_limit
        self.nano_cpus = nano_cpus

    @staticmethod
    def container_name(slug: str) -> str:
        return f"renderer-{slug}"

    def renderers(self) -> dict[str, Renderer]:
        containers = self.client.containers.list(all=True, filters={"label": f"{MANAGED_LABEL}=1"})
        return {
            slug: Renderer(slug, container.status == "running")
            for container in containers
            if (slug := container.labels.get(SLUG_LABEL))
        }

    def ensure(self, assignment: Assignment) -> None:
        import docker

        name = self.container_name(assignment.slug)
        try:
            container = self.client.containers.get(name)
        except docker.errors.NotFound:
            self.client.containers.run(
                self.image,
                name=name,
                detach=True,
                network_mode="host",
                restart_policy={"Name": "unless-stopped"},
                mem_limit=self.mem_limit,
                nano_cpus=self.nano_cpus,
                labels={MANAGED_LABEL: "1", SLUG_LABEL: assignment.slug},
                environment={
                    "BROKER_URL": self.broker_url,
                    "DEVICE_NAME": assignment.slug,
                    "CLAIM_TOKEN": assignment.claim_token,
                },
                volumes={
                    f"{name}-data": {"bind": "/data", "mode": "rw"},
                    f"{name}-key": {"bind": "/keys", "mode": "rw"},
                },
            )
            return
        if container.status != "running":
            container.start()

    def remove(self, slug: str) -> None:
        import docker

        name = self.container_name(slug)
        with contextlib.suppress(docker.errors.NotFound):
            self.client.containers.get(name).remove(force=True, v=True)
        # Named volumes aren't removed with the container, and they are the renderer's identity.
        for volume in (f"{name}-data", f"{name}-key"):
            with contextlib.suppress(docker.errors.NotFound):
                self.client.volumes.get(volume).remove(force=True)
