# Running the render pool and providers with Docker

Two compose projects: `docker-compose.renderer.yml` (project
`renderer`) runs the render pool (`worker-1`, `worker-2`, `ticker`) and `docker-compose.providers.yml` (project
`providers`) one service per provider. Containers are named `<project>-<service>-1`, for example `renderer-ticker-1` and
`providers-tfl-1`. Both run on the same host as countdown-server and its Redis, with host networking, so Redis is at `127.0.0.1:6379` and
metrics go to the host's Fluent Bit at `127.0.0.1:4318` (without one the workers log a warning each minute). How
rendering works: [How rendering works](../../docs/stateless-renderers.md).

## The images

Every release builds `ghcr.io/nvorkinn/countdown-renderer` (the pool) and `ghcr.io/nvorkinn/countdown-providers` from
that release's wheels, tagged `<tag>` and `latest`. The renderer image's entrypoint is `countdown-render-worker`; the
ticker overrides it with `countdown-render-ticker`. It keeps nothing: no identity, no volume. To build one locally,
from the repository root:

```sh
./dev/build_dist.sh   # the wheels and each image's requirements, in dist/
docker build -f renderer/Dockerfile -t countdown-renderer:dev .   # or providers/Dockerfile
```

## Adding a screen

Nothing to deploy. Pair the screen on the broker's config page: the broker lists it in `v1:render:screens`, and the pool
draws it from the next minute on (at once after a config save).

## Deploying

`.github/workflows/deploy-renderers.yml` runs `deploy.sh <tag>` on the runner against one environment's Docker daemon over
ssh (`DOCKER_HOST`), so the host needs only Docker. The script pins the images to the release, brings each project up in
turn with `--wait` (first taking down the old `countdown-renderers` project, which was both in one file), and removes
leftover `renderer-<slug>` containers (label `countdown.managed=1`) and their `-data` and `-key` volumes. It then removes
older tags of the two images, keeping the running one and the newest other (to roll back to), and prunes dangling
layers, so the host's disk doesn't fill with every release's images. It checks out the tag it deploys, so the compose
files are that release's.

There are two environments, each its own host with its own countdown-server and Redis:

- **dev-qa**: `release.yml` calls the deploy after it has built and published a release, with no approval. The release
  stays a pre-release.
- **production**: only `promote.yml` (Actions > Promote > Run workflow, input `tag`) deploys there. It needs a tag that is
  on `main` and deployed to dev-qa, waits for a reviewer of the `approve-production` environment, runs the same deploy,
  and then marks the release as the latest.

To roll back, promote an older tag. To redeploy a release to dev-qa, re-run its deploy job from the Actions tab.

Set up once in the repo's settings (see also the [README](../../../README.md#one-time-github-setup)):

- Environments `dev-qa` and `production` (restricted to `main` and `v*` tags), no reviewers on either, and
  `approve-production` with the required reviewers.
- In each of `dev-qa` and `production`, secrets `DEPLOY_USER`, `DEPLOY_HOST`, `ORACLE_SSH_TOKEN` (the private key) and
  `DEPLOY_SSH_KNOWN_HOSTS`, the same as countdown-server's for that host. The user must be in the `docker` group.
- In each, secrets `REDIS_PASSWORD` and `TFL_APP_KEY`. `REDIS_PASSWORD` must be the same as countdown-server's
  `secrets/<env>/redis.env` for that environment, or the pool and providers can't log in; a missing one fails the deploy.
- Optionally in each, variables `COUNTDOWN_AUTH_HOST` and `COUNTDOWN_SITE_NAME`: the pairing screen's website and the
  host its QR code opens (`https://<COUNTDOWN_AUTH_HOST>/pair?code=...`). Unset, they are `auth.nikolaivorkinn.com` and
  `nikolaivorkinn.com`. Only the render workers read them.
- Deploy countdown-server to an environment first: Redis is deployed there, and this stack connects to it on
  `127.0.0.1:6379`.

## Providers

One service per provider: `tfl`, `tfl-notices`, `entur`, `entur-situations`, `weather`, `met-office`,
`bank-holidays`, `floods`, `metalerts` and `locations`. The pool reads what they write, so a panel has nothing to draw until its provider has written.
Providers fetch as they start, so that takes seconds. See [the providers](../../providers/README.md) for what each
writes and how to check.

## Checking on the host

```sh
docker compose -p renderer ps
docker compose -p providers ps
docker logs -f renderer-worker-1-1  # warnings and errors only: no line per frame
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli HGETALL v1:render:screens'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli HMGET v1:frame:<device id> etag rendered_at'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli LLEN v1:render:jobs'   # 0, between minutes
```
