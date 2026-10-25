# pi-dashboard

[![codecov](https://codecov.io/gh/nvorkinn/pi-dashboard/graph/badge.svg)](https://codecov.io/gh/nvorkinn/pi-dashboard)

Draws the screens of an e-paper dashboard. ESP32 screens poll [countdown-server](https://github.com/nvorkinn/countdown-server)
for frames; this repository's render pool draws every screen from its config and panel data in Redis and writes the
frame back to Redis, and its providers fetch the panel data.

- [`countdown/`](countdown/) is the Python code: how it works, and the render pool and providers in it.
- [`countdown/renderer/deploy/`](countdown/renderer/deploy/) is how both run, as Docker containers next to the broker.

## Releasing and promoting

Two environments, each one host running [countdown-server](https://github.com/nvorkinn/countdown-server)'s stack (with
the Redis this repository's render pool and providers use) and this repository's containers: **dev-qa** and
**production**. A tag is built once and the same tag is promoted.

- **Release** (dev-qa, no approval): push a `v*` tag on a commit that's on `main`.
  `.github/workflows/release.yml` builds the wheels and images, creates the GitHub release and deploys the render pool and providers to dev-qa
  (`.github/workflows/deploy-renderers.yml`). Starting a release is the decision to deploy it there. A release never
  touches production. The tag is the version: it's stamped into the wheels at build time, so the `version` in
  `countdown/pyproject.toml` is a placeholder.
- **Promote** (production): once a release has been tried on dev-qa, run Actions > Promote > Run workflow with its tag
  (`vX.Y.Z`). `.github/workflows/promote.yml` checks that the tag's commit is on `main` and that the tag has a successful
  dev-qa deployment, waits for a reviewer of the `approve-production` environment, deploys the same tag's images to
  production. One promotion runs at a time.
- **Roll back** by promoting an older tag the same way (a tag that was in production before passes the check too).
  The deploy takes the compose files from that tag.

### One-time GitHub setup

Settings > Environments:

| Environment | Reviewers | Deployment branches and tags | Secrets | Variables |
| --- | --- | --- | --- | --- |
| `dev-qa` | none | any (releases run from `v*` tags) | `DEPLOY_HOST`, `DEPLOY_USER`, `ORACLE_SSH_TOKEN`, `DEPLOY_SSH_KNOWN_HOSTS`, `REDIS_PASSWORD`, `TFL_APP_KEY` | `COUNTDOWN_AUTH_HOST`, `COUNTDOWN_SITE_NAME` |
| `production` | none (the approval is `approve-production`'s) | `main` and `v*` tags only | the same six, for the production host | the same two, for production |
| `approve-production` | required reviewers (you) | `main` | none | none |

- `DEPLOY_*` and `ORACLE_SSH_TOKEN` are the host's, as for countdown-server; `DEPLOY_SSH_KNOWN_HOSTS` holds its host key.
- `REDIS_PASSWORD` must equal that environment's countdown-server `secrets/<env>/redis.env`. A missing password fails
  the deploy rather than start anything unauthenticated.
- `COUNTDOWN_AUTH_HOST` and `COUNTDOWN_SITE_NAME` are what the pairing screen shows and its QR code opens: the
  environment's auth host (for example `auth.dev.example.org`) and website name. Leave them unset for
  `auth.nikolaivorkinn.com` and `nikolaivorkinn.com`.
- Deploy countdown-server to an environment **before** this repository's first deploy there: Redis lives with it, and
  the render pool and providers need it.
- The old `deploy-renderers` environment is no longer used and can be deleted.

## PR status check

Branch protection requires one check, **Global PR Status**: `create_global_pr_status.yml` opens it on a PR's head
commit, and `all-checks.yml` completes it, and approves the PR, once every other workflow and check on that commit has
passed. Both call the reusable workflows in [`nvorkinn/.github`](https://github.com/nvorkinn/.github), pinned to a
commit. When you add a workflow that runs on pull requests, add its `name` to `workflows:` in `all-checks.yml`.
