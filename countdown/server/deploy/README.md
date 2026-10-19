# Running the render pool with Docker

Every screen (the ESP32) only shows frames: the render pool draws them, and the auth broker hands each one to its
screen. The pool runs on the same server as the broker: two workers and a ticker, compose services in
`docker-compose.yml`, from the `ghcr.io/nvorkinn/countdown-server` image ([Stateless renderers](../../docs/stateless-renderers.md)).

## The image

Every release builds it from that release's own wheels and pushes it as
`ghcr.io/nvorkinn/countdown-server:<tag>` and `:latest`. Its default entrypoint is a render worker
(`countdown-render-worker`); the ticker is `countdown-render-ticker`. It keeps nothing: no identity, no key, no volume.

To build it locally, from `countdown/`:

```sh
uv build --wheel --all-packages
docker build -f server/Dockerfile -t countdown-server:dev .
```

## Adding a screen

Set up the screen (the ESP32) and pair it on the config page. The broker writes its config to the store and lists it
in `v1:render:screens`, and the pool draws it from the next minute on (at once, on a config save). Nothing here
changes: there's no container per screen.

## Deploying

`release.yml`'s `deploy-renderers` job runs once a release's assets are uploaded. It waits for approval of the
`deploy-renderers` environment, then runs `deploy.sh <tag>` on the runner against the host's Docker daemon over
ssh (`DOCKER_HOST=ssh://...`), so the host needs nothing installed but Docker. That pins the providers' and the pool's
images to the release and brings them up.

It also removes what's left of the per-screen renderers (stateless step 9): every `renderer-<slug>` container the
orchestrator made (labelled `countdown.managed=1`), and then their `renderer-<slug>-data` and `renderer-<slug>-key`
volumes (only volumes no container uses). Once they're gone it does nothing.

Set up once, in the repo's Settings:

- An environment `deploy-renderers`, with yourself as a required reviewer (without one, it deploys unasked).
- Secrets `DEPLOY_USER`, `DEPLOY_HOST`, `ORACLE_SSH_TOKEN` (the private key) and `DEPLOY_SSH_KNOWN_HOSTS`. They're
  the same values as auth-broker's. The user must be able to use Docker (be in the `docker` group).
- Secrets `REDIS_PASSWORD` (the same as auth-broker's `secrets/redis.env`) and `TFL_APP_KEY`.

## The render pool

`render-worker-1` and `render-worker-2` (`countdown-render-worker`) and `render-ticker` (`countdown-render-ticker`),
on the host's network, with `REDIS_PASSWORD`. They draw every screen in `v1:render:screens`, which auth-broker writes
for every paired device. Their metrics go to the host's Fluent Bit at `127.0.0.1:4318`; without one listening they
log a warning each minute.

To check on the host:

```sh
docker logs countdown-renderers-render-worker-1-1      # warnings and errors only: no line per frame
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli HGETALL v1:render:screens'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli HMGET v1:frame:<device id> etag rendered_at'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli LLEN v1:render:jobs'   # 0, between minutes
```

## Providers

The compose file also has a service per provider (`provider-tfl`, `provider-tfl-notices`, `provider-entur`,
`provider-entur-situations`, `provider-weather`, `provider-met-office`, `provider-bank-holidays`, `provider-floods`,
`provider-metalerts`), which every deploy starts and upgrades. They need the repo secrets `REDIS_PASSWORD` and
`TFL_APP_KEY`. See [`../../providers/README.md`](../../providers/README.md#enabling-in-production).

The notice boards read the bank holidays only from the store, so after the first deploy with `provider-bank-holidays`,
check its key is there ([Bank holidays](../../providers/README.md#bank-holidays) says how).

The render pool reads what the providers write, so a provider must be running, and its keys in Redis, before the
pool can draw from them. A deploy starts both together, and a provider fetches as it starts, so that's seconds;
to check after a deploy, e.g. that the notice board's Entur disruptions are there (`provider-entur-situations`):

```sh
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 --scan --pattern "v1:notices:entur*"'
```

One key per Entur stop any screen shows; none means the provider isn't running (its log says why), and the boards
leave Entur's disruptions out meanwhile.

The flood warnings too are read only from the store: after the first deploy with `provider-floods` (and auth-broker's
postcode subscriptions), check its keys are there ([Flood warnings](../../providers/README.md#flood-warnings) says how).

So are the Norwegian boards' MET weather warnings, from `provider-metalerts`: after its first deploy (and auth-broker's
postcode subscriptions), check its key is there ([Norway's weather warnings](../../providers/README.md#norways-weather-warnings)
says how).

## Day to day

```sh
docker logs -f countdown-renderers-render-worker-1-1    # what a worker is doing, on the host
docker compose -p countdown-renderers ps
```

To hold back or roll back, re-run an older release's `deploy-renderers` job from the Actions tab.
A release from before the per-screen renderers were retired brings the orchestrator back, which needs the
`ORCHESTRATOR_TOKEN` secret and the broker's orchestrator and claim endpoints (gone once auth-broker retires them too),
and makes every screen a new renderer: roll back the render pool's code instead, with a release of this one's shape.
