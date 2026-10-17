# Running renderers with Docker

A screen that only shows frames (the ESP32) needs a
renderer: a `countdown-server` process that draws its dashboard and hands each frame to the auth
broker, where the screen fetches it. These run on the same server as the broker, one container per
screen, from the `ghcr.io/nvorkinn/countdown-server` image.

## The image

Every release builds it from that release's own wheels and pushes it as
`ghcr.io/nvorkinn/countdown-server:<tag>` and `:latest`.

To build it locally, from `countdown/`:

```sh
uv build --wheel --all-packages
docker build -f server/Dockerfile -t countdown-server:dev .
```

## Adding a screen

Set up the screen (the ESP32), then choose a renderer slug on the config page. The
orchestrator starts its renderer, and stops it after a day without the screen. To remove one, delete its
assignment through the config page; the orchestrator removes the renderer **with its volumes** (identity and
key).

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
ssh (`DOCKER_HOST=ssh://...`), so the host needs nothing installed but Docker. That pins the orchestrator and
renderer image to the release, then the orchestrator updates assigned renderers.

Set up once, in the repo's Settings:

- An environment `deploy-renderers`, with yourself as a required reviewer (without one, it deploys unasked).
- Secrets `DEPLOY_USER`, `DEPLOY_HOST`, `ORACLE_SSH_TOKEN` (the private key) and `DEPLOY_SSH_KNOWN_HOSTS`. They're
  the same values as auth-broker's. The user must be able to use Docker (be in the `docker` group).
- Secret `ORCHESTRATOR_TOKEN`: the orchestrator's credential on the broker, which the deploy hands to it.

## Providers

The compose file also has a service per provider (`provider-tfl`, `provider-entur`, `provider-weather`), which every deploy
starts and upgrades. They need the repo secrets `REDIS_PASSWORD` and `TFL_APP_KEY`. See [`../../providers/README.md`](../../providers/README.md#enabling-in-production).

## Day to day

```sh
docker logs -f renderer-<slug>                 # what a renderer is doing, on the host
docker ps --filter label=countdown.managed=1
```

The first deploy removes the former `nikolai` Compose service. If the Compose file declares no named volumes,
`deploy.sh` skips volume cleanup to preserve existing data; any old `countdown-renderers_nikolai*` volumes can
be removed manually after confirming they are no longer needed.

To hold back or roll back, re-run an older release's `deploy-renderers` job from the Actions tab.
