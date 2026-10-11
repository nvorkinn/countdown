# Running renderers with Docker

A screen that only shows frames -- a Pi running `countdown-client`, or the ESP32 -- needs a
renderer: a `countdown-server` process that draws its dashboard and hands each frame to the auth
broker, where the screen fetches it. These run on the same server as the broker, one container per
screen, from the `ghcr.io/nvorkinn/countdown-server` image.

## The image

Every release builds it from that release's own wheels and pushes it as
`ghcr.io/nvorkinn/countdown-server:<tag>`. `:latest` moves when a release is promoted to the
GitHub release marked latest -- the same release the Pis update to -- so a renderer on `:latest`
never runs an untested pre-release.

To build it locally, from `countdown/`:

```sh
uv build --wheel --all-packages
docker build -f server/Dockerfile -t countdown-server:dev .
```

## Adding a screen

Most renderers aren't listed here: the **orchestrator** (the first service in `docker-compose.yml`, see
`../../orchestrator/README.md`) starts one for every screen that's been set up on the config page, and stops it
after a day without the screen. Only list one below by hand for a renderer that should exist regardless. Slugs
are unique across the broker, so a hand-listed `DEVICE_NAME` mustn't be one a user could also pick.

The renderers listed here are services in `docker-compose.yml`, and a release deploys them (below).

1. Copy the `nikolai` service and its two volumes in `docker-compose.yml`, renaming all three and setting
   `DEVICE_NAME` (it labels the device on the broker).
2. Merge it and cut a release. The deploy creates the renderer, and updates the others.
3. Set up the screen (`countdown-client` or the ESP32). The renderer and the screen each wait in
   the broker's pool until the other arrives, then become one device; `flask devices pending` on
   the broker shows who's waiting. The renderer then draws the pairing code for the screen.

To remove one, delete its service. The next deploy stops and deletes it, **with its volumes**: the identity and
its key. Adding it back registers a new device.

Each renderer's identity, `.auth_broker_device`, lives in its volume (`/data`). Keep the volume
and the renderer stays the same device across restarts and upgrades; remove it and the renderer
registers as a new one.

## The credentials key

`.auth_broker_device` is encrypted in the `/data` volume, so a copy of that volume (a backup, an export) isn't a
copy of the device's identity. The key is made by the renderer on its first start, into the screen's second
volume (`<name>-key`, mounted at `/keys`), and needs no setup. Keep both volumes together: with the key volume
gone the encrypted file can't be read, and the renderer refuses to start rather than quietly becoming a new
device. To start over, remove `.auth_broker_device` from the data volume.

The key protects the data volume on its own, not the host: anyone who can read both volumes has both. To keep the
key off the host's volumes altogether, mount it as a Docker secret named `credentials-key` instead, which the
renderer uses in preference.

There's no Fluent Bit in the container. Its health metrics go to the host's own Fluent Bit at `127.0.0.1:4318` (the
compose file uses host networking so the container can reach it), which also ships the host's logs; the renderer
needs no token. Without a Fluent Bit listening there it logs a warning each minute.

## Deploying

`release.yml`'s `deploy-renderers` job runs once a release's assets are uploaded. It waits for approval of the
`deploy-renderers` environment, then runs `deploy.sh <tag>` on the runner against the host's Docker daemon over
ssh (`DOCKER_HOST=ssh://...`), so the host needs nothing installed but Docker. That pins every renderer to the
release's image, pulls it, and brings up the renderers that are new or changed. The release is still a
pre-release then, so renderers can be ahead of the Pis until it's promoted.

Set up once, in the repo's Settings:

- An environment `deploy-renderers`, with yourself as a required reviewer (without one, it deploys unasked).
- Secrets `DEPLOY_USER`, `DEPLOY_HOST`, `ORACLE_SSH_TOKEN` (the private key) and `DEPLOY_SSH_KNOWN_HOSTS`. They're
  the same values as auth-broker's. The user must be able to use Docker (be in the `docker` group).
- Secret `ORCHESTRATOR_TOKEN`: the orchestrator's credential on the broker, which the deploy hands to it.

## Day to day

```sh
docker logs -f countdown-renderers-<name>-1   # what a renderer is doing, on the host
docker ps --filter label=com.docker.compose.project=countdown-renderers
```

To hold back or roll back, re-run an older release's `deploy-renderers` job from the Actions tab.
