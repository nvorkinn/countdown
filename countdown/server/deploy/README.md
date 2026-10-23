# Running the render pool and providers with Docker

`docker-compose.yml` runs the render pool (`render-worker-1`, `render-worker-2`, `render-ticker`) and one service per
provider, on the same host as auth-broker and its Redis. All use host networking, so Redis is at `127.0.0.1:6379` and
metrics go to the host's Fluent Bit at `127.0.0.1:4318` (without one the workers log a warning each minute). How
rendering works: [How rendering works](../../docs/stateless-renderers.md).

## The images

Every release builds `ghcr.io/nvorkinn/countdown-server` (the pool) and `ghcr.io/nvorkinn/countdown-providers` from
that release's wheels, tagged `<tag>` and `latest`. The server image's entrypoint is `countdown-render-worker`; the
ticker overrides it with `countdown-render-ticker`. It keeps nothing: no identity, no volume. To build one locally,
from `countdown/`:

```sh
./dev/build_dist.sh   # the wheels and each image's requirements, in dist/
docker build -f server/Dockerfile -t countdown-server:dev .   # or providers/Dockerfile
```

## Adding a screen

Nothing to deploy. Pair the screen on the broker's config page: the broker lists it in `v1:render:screens`, and the pool
draws it from the next minute on (at once after a config save).

## Deploying

`release.yml`'s `deploy-renderers` job waits for approval of the `deploy-renderers` environment, then runs
`deploy.sh <tag>` on the runner against the host's Docker daemon over ssh (`DOCKER_HOST`), so the host needs only
Docker. The script pins the images to the release, brings the stack up with `--wait`, and removes leftover
`renderer-<slug>` containers (label `countdown.managed=1`) and their `-data` and `-key` volumes. It then removes
older tags of the two images, keeping the running one and the newest other (to roll back to), and prunes dangling
layers, so the host's disk doesn't fill with every release's images.

Set up once in the repo's settings:

- The environment `deploy-renderers`, with yourself as required reviewer (without one it deploys unasked).
- Secrets `DEPLOY_USER`, `DEPLOY_HOST`, `ORACLE_SSH_TOKEN` (the private key) and `DEPLOY_SSH_KNOWN_HOSTS`, the same as
  auth-broker's. The user must be in the `docker` group.
- Secrets `REDIS_PASSWORD` (the same as auth-broker's `secrets/redis.env`) and `TFL_APP_KEY`. A missing password fails
  the deploy rather than start anything unauthenticated.

To hold back or roll back, re-run an older release's `deploy-renderers` job from the Actions tab.

## Providers

One service per provider: `provider-tfl`, `provider-tfl-notices`, `provider-entur`, `provider-entur-situations`,
`provider-weather`, `provider-met-office`, `provider-bank-holidays`, `provider-floods`, `provider-metalerts` and
`provider-locations`. The pool reads what they write, so a panel has nothing to draw until its provider has written.
Providers fetch as they start, so that takes seconds. See [the providers](../../providers/README.md) for what each
writes and how to check.

## Checking on the host

```sh
docker compose -p countdown-renderers ps
docker logs -f countdown-renderers-render-worker-1-1      # warnings and errors only: no line per frame
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli HGETALL v1:render:screens'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli HMGET v1:frame:<device id> etag rendered_at'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli LLEN v1:render:jobs'   # 0, between minutes
```
