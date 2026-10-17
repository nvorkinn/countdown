"""Where renderers run. The orchestrator only knows this interface; DockerRuntime is the one implementation."""

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import docker
from docker.types import LogConfig

if TYPE_CHECKING:
    from docker.models.containers import Container

    from countdown_orchestrator.models import Assignment

# On every container the orchestrator makes, so it only ever touches (and lists) its own.
MANAGED_LABEL = "countdown.managed"
SLUG_LABEL = "countdown.slug"
# The image a renderer was made from, so a release's deploy can bring it up to date.
IMAGE_LABEL = "countdown.image"
# The store a renderer reads (its REDIS_URL), on renderers that read one: changing it remakes them, like a new image.
STORE_LABEL = "countdown.store"
LOG_ROTATION = {"max-size": "10m", "max-file": "3"}
# What a renderer is given as its claim token once the screen is claimed and the broker has cleared the real one. It
# still refuses to start without some value, but then ignores it: its identity is in its volumes.
CLAIMED = "already-claimed"


@dataclass(frozen=True)
class Renderer:
    """A renderer as it is on the host: whether it's running, and whether it's from the current image."""

    slug: str
    running: bool
    up_to_date: bool = True


class Runtime(Protocol):
    """What the orchestrator needs from wherever renderers run."""

    def renderers(self) -> dict[str, Renderer]:
        """Every renderer the orchestrator has made, by slug."""
        ...

    def ensure(self, assignment: Assignment) -> None:
        """Make the renderer if it doesn't exist, and start it if it isn't running. Safe to repeat.

        It's made from the current image, replacing the container (never its volumes) if it is from an older one.
        """
        ...

    def stop(self, slug: str) -> None:
        """Stop the renderer, keeping its container and volumes so it can be started again."""
        ...

    def remove(self, slug: str) -> None:
        """Stop the renderer and delete it with its volumes (its identity and the key to it)."""
        ...


class DockerRuntime:
    """One container per renderer, from the countdown-server image.

    Shaped like the services in server/deploy/docker-compose.yml: host networking (for the host's Fluent Bit) and
    a volume each for the identity and its key.
    """

    def __init__(  # noqa: PLR0913 -- each is one setting of the containers it makes, all but two keyword-only
        self,
        image: str,
        broker_url: str,
        *,
        mem_limit: str = "256m",
        nano_cpus: int = 500_000_000,
        client: docker.DockerClient | None = None,
        store_url: str = "",
        store_password: str = "",
    ) -> None:
        """Run renderers from `image`, pointed at the broker at `broker_url`.

        Each is limited to `mem_limit` of memory and `nano_cpus` billionths of a CPU. `client` is the Docker
        client, by default the one the environment configures. With `store_url` (and `store_password`), renderers
        read departures and weather from that Redis (as REDIS_URL and REDIS_PASSWORD) rather than calling the APIs;
        without, they call the APIs (see countdown_core.store.reader).
        """
        if client is None:
            client = docker.from_env()
        self.client = client
        self.image = image
        self.broker_url = broker_url
        self.mem_limit = mem_limit
        self.nano_cpus = nano_cpus
        self.store_url = store_url
        self.store_password = store_password

    @staticmethod
    def container_name(slug: str) -> str:
        """Name the renderer's container; its volumes' names start with this too."""
        return f"renderer-{slug}"

    def renderers(self) -> dict[str, Renderer]:
        """Every renderer container this orchestrator made (by its label), by slug."""
        containers = self.client.containers.list(all=True, filters={"label": f"{MANAGED_LABEL}=1"})
        return {
            slug: Renderer(
                slug,
                running=container.status == "running",
                up_to_date=self._is_current(container),
            )
            for container in containers
            if (slug := container.labels.get(SLUG_LABEL))
        }

    def ensure(self, assignment: Assignment) -> None:
        """See Runtime.ensure: a container from an older image is replaced, a stopped one started."""
        name = self.container_name(assignment.slug)
        try:
            container = self.client.containers.get(name)
        except docker.errors.NotFound:
            self._run(assignment)
            return
        if not self._is_current(container):
            # Only the container: the volumes are the renderer's identity, so it stays the same device.
            container.remove(force=True)
            self._run(assignment)
        elif container.status != "running":
            container.start()

    def _is_current(self, container: Container) -> bool:
        """Whether the container is from the current image and reads the current store (or, like this, none)."""
        labels = container.labels
        return labels.get(IMAGE_LABEL) == self.image and labels.get(STORE_LABEL, "") == self.store_url

    def _run(self, assignment: Assignment) -> None:
        name = self.container_name(assignment.slug)
        labels = {MANAGED_LABEL: "1", SLUG_LABEL: assignment.slug, IMAGE_LABEL: self.image}
        environment = {
            "BROKER_URL": self.broker_url,
            "DEVICE_NAME": assignment.slug,
            "CLAIM_TOKEN": assignment.claim_token or CLAIMED,
        }
        if self.store_url:
            labels[STORE_LABEL] = self.store_url
            environment["REDIS_URL"] = self.store_url
            if self.store_password:
                environment["REDIS_PASSWORD"] = self.store_password
        self.client.containers.run(
            self.image,
            name=name,
            detach=True,
            network_mode="host",
            restart_policy={"Name": "unless-stopped"},
            mem_limit=self.mem_limit,
            nano_cpus=self.nano_cpus,
            labels=labels,
            # What the host's log collector names the stream (renderer-<slug>), as for the compose services; the
            # json-file driver is the one it tails. Rotated, so a chatty renderer can't fill the disk.
            log_config=LogConfig(type=LogConfig.types.JSON, config={"tag": "{{.Name}}", **LOG_ROTATION}),
            environment=environment,
            volumes={
                f"{name}-data": {"bind": "/data", "mode": "rw"},
                f"{name}-key": {"bind": "/keys", "mode": "rw"},
            },
        )

    def stop(self, slug: str) -> None:
        """See Runtime.stop. A renderer that doesn't exist is already stopped."""
        with contextlib.suppress(docker.errors.NotFound):
            # Stopped by Docker, `unless-stopped` keeps it down across a reboot of the host too.
            self.client.containers.get(self.container_name(slug)).stop()

    def remove(self, slug: str) -> None:
        """See Runtime.remove: the container, then its named volumes. Whatever is already gone is skipped."""
        name = self.container_name(slug)
        with contextlib.suppress(docker.errors.NotFound):
            self.client.containers.get(name).remove(force=True, v=True)
        # Named volumes aren't removed with the container, and they are the renderer's identity.
        for volume in (f"{name}-data", f"{name}-key"):
            with contextlib.suppress(docker.errors.NotFound):
                self.client.volumes.get(volume).remove(force=True)
