# countdown

[![codecov](https://codecov.io/gh/nvorkinn/countdown/graph/badge.svg)](https://codecov.io/gh/nvorkinn/countdown)

The Python code that draws the 7.5" e-paper dashboard: TfL and Entur departures, weather, a notice board, Glowmarkt
energy use, Spotify and a developer panel, all configured through [countdown-server](https://github.com/nvorkinn/countdown-server).
ESP32 screens fetch the frames from the broker; nothing here talks to a screen or to the broker.

This repository and countdown-server share only Redis. For the whole system (who calls whom, which secret exists when) see
countdown-server's [How the pieces fit together](https://github.com/nvorkinn/countdown-server/blob/main/docs/architecture.md).

## Packages

A uv workspace; `uv sync` installs everything editable.

- `core/` (`countdown-core`): the drawing: API clients that read the store, panels, layouts and the render function.
- `renderer/` (`countdown-renderer`): the render pool, `countdown-render-worker` and `countdown-render-ticker`; see
  [How rendering works](docs/stateless-renderers.md).
- `providers/` (`countdown-providers`): one process per API, fetching what screens subscribe to into the store; see
  [its README](providers/README.md).
- `api/`: the [countdown-api](https://github.com/nvorkinn/countdown-api#readme) submodule, the store contract.

Running them is in [`renderer/deploy/`](renderer/deploy/).

## How it works

Every frame is one call of `render(device_id, now)` (`core/src/countdown_core/render.py`). It reads the screen's config
from Redis, builds the API clients fresh, has each read its key, draws the frame, and writes it to `v1:frame:<device id>`
if it changed. Rendering is described in [How rendering works](docs/stateless-renderers.md).

**The config.** The broker writes each screen's config to `v1:config:<device id>` (`store/config.py`) as the contract's
`ConfigResponse`, without secrets or a pairing code. While a pairing code is live the broker writes
`v1:pairing:<device id>` instead (`store/pairing.py`) and the render draws the pairing screen. A config that doesn't
validate fails the render and the screen keeps its frame; a field the broker adds is ignored.

**API clients** (`*_client.py`, `core/api_registry.py`). Each API has an `AbstractClient` subclass: departures (TfL and
Entur), Open-Meteo weather, Glowmarkt energy, Spotify and Apple Music, the developer panel and the notice board. They
read only the store, which the [providers](providers/README.md) and countdown-server's providers fill: the render calls no
upstream API and holds no API token. A client whose key has expired returns a "Not available" panel; one that fails
for another reason shows "Could not connect", and an API that isn't set up shows "Not configured". Redis not answering
fails the render. One broken API never takes the others down.

**Notice board** (`notice_board_client.py`, `notices/`). Reads TfL line and station status, Met Office warnings,
Environment Agency flood warnings, TfL road incidents, bank holidays and clock changes from the store, plus Entur
disruptions and, for a Norwegian postcode, MET Norway's warnings. Where a postcode is comes from `v1:location:<place>`
(`provider-locations`). Notices are ranked by severity, and planned works only fill leftover rows. An expired source
adds nothing, and if every source has nothing the board says "Not available" rather than "All clear".

**Layout** (`rendering/`). `LAYOUTS` holds layouts by canvas (800x480 landscape or 480x800 portrait, as the screen is
hung, or a 1872x1404 HD screen either way), each with a variant per combination of enabled APIs. The HD portrait
layout is the portrait one with each panel drawn bigger at the screen's own resolution (`utils/scaled_draw.py`:
the stops two and a half times as big, the rest twice), so text is crisp and as big as on the 7.5" screens. The HD weather spans the whole width in one of four designs (`weather/hd_weather_panel.py`: columns, timeline, overview or poster), the config's `weather.design`, drawn at the stops' scale so its text is their size. Spotify's now page is wide there (each style's `wide` page: what's playing and what's up next side by side, 150 tall) and the notice board a table (`HdNoticeBoardPanel`: taller rows, the text column aligned). The developer panel has no area of its own
(`place_developer_panel`): while Spotify shows a song it's a strip above it (open PRs and builds in one row, or a
release's jobs in a row of boxes), and otherwise it takes Spotify's whole area.

The HD landscape layout (`layouts/hd_landscape.py`, `sidebar`) has a black sidebar down the left edge: the time and date
over the weather now and a row for each later page, white on black (`weather/hd_sidebar_panel.py`). Beside it are the
two stops filling the column (two and a quarter times as big), the coming hours in a strip, the energy chart and Spotify
side by side, and the notices.

**The frame.** Frames are drawn upright; a portrait frame is turned a quarter into the panel's native landscape picture
(`rendering.to_native`), then encoded in the screen's `format` (`rendering.encoding`): `mono-1bpp-packed` is its raw 1-bit buffer, `png` a PNG image. If every API only has a
message to show, a "Nothing to show yet" screen replaces the dashboard. Partial refresh is the firmware's business: the
server always sends the whole frame.

**Language, time zone and clock.** The config's `language` (`en` or `nb`), `timezone` and `clock_format` (`24h` or
`12h`) apply per screen. Every drawn text is English in the code, marked `_()` (or `N_()`), and translated from
`core/src/countdown_core/locale/<language>.po`; an untranslated text stays English. After adding or changing a text,
run `sh dev/update_translations.sh` from the repository root. Dates go through Babel (`i18n.format_date`). A Norwegian
postcode (`notice_board.country` is `NO`) gets Norway's notices: public holidays are worked out here and the rest comes
from the store.

## The store contract

The keys, models, fixtures and the enqueue script both repositories use are
[countdown-api](https://github.com/nvorkinn/countdown-api#readme): its README lists every key. `store/keys.py`
re-exports them beside the keys only this repository uses, and `tests/test_store_contract.py` round-trips this side's
readers and writers through the shared fixtures. The broker may add fields (ignored), but a value outside what the
contract lists is invalid and fails the render.

`api/` is a git submodule and a uv workspace member. To move to a newer contract:

```sh
git submodule update --remote api
git add api && git commit
```

A fresh checkout gets the recorded commit. CI and the release run `git submodule update --init --remote` first, so they
use countdown-api's `main`. A release stamps its version on every workspace package, countdown-api included, and the
images install only these wheels, never an index.

## Running a worker in development

Everything a worker draws is in Redis, so point it at a Redis that the broker and providers fill (a local countdown-server,
and the providers). Put the compose services' environment in `dev/.env`:

```bash
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_PASSWORD=<the local Redis's password>
WORKER_NAME=render-worker-dev
LOG_LEVEL=DEBUG
```

Run `uv run --env-file dev/.env countdown-render-worker` from the repository root, with `countdown-render-ticker` beside it or
a screen queued by hand (`LPUSH v1:render:jobs <device id>`). Only screens in `v1:render:screens` are drawn. Never point
a development worker at production's Redis: it would write its frames to the screens. Without a Fluent Bit on
`127.0.0.1:4318` the worker logs a metrics warning each minute.

## Visual regression tests

Every test that takes the `snapshot` fixture (`pytest -m snapshot`) renders a screen or panel and compares it, exactly,
with a golden PNG in `tests/images/`. Text is drawn differently on macOS, on a Linux host and on another Pillow or font
build, so the goldens are drawn and compared only in one pinned Linux image (`dev/snapshots.Dockerfile`: base image
digest, uv, the locked packages, Material Symbols at a pinned commit). It renders byte-identically on amd64 and arm64.
CI runs the same image in its `snapshots` job; the other tests run bare.

```bash
dev/snapshots.sh test      # compare (needs Docker)
dev/snapshots.sh update    # redraw the goldens
```

A mismatch writes `tests/images/_failures/<name>.actual.png` and `<name>.diff.png`. Review the changed PNGs (GitHub's
"Files changed" shows image diffs) and commit them with the code. To add a scenario, add a function that builds its
panels and calls `snapshot.assert_matches("some_name", image)`, then run `update` once. Bump the image when the base
image or the font should change: its digest and the font's hash are `ARG`s at the top of the Dockerfile, and the goldens
are redrawn with it.

## Logs and metrics

The pool and the providers log one JSON object per line on stderr (`countdown_core/core/log_format.py`): `level`, `_msg`,
`logger`, `source` and `exception`. The pool adds `worker`; the providers add `device` (`DEVICE_NAME`, else the
hostname, sanitised to `[a-z0-9_-]`). There is no timestamp: the container's log driver stamps each line. `LOG_LEVEL`
sets the level (default INFO). Nothing logged says where a screen is: no postcode, place or coordinates, nor a URL with
one in it.

The host's Fluent Bit ships the containers' logs to VictoriaLogs, naming each stream after the container's logging tag
(`render-worker-1`, `provider-tfl`...). Workers send OTLP metrics to `http://127.0.0.1:4318/v1/metrics`, listed in
[How rendering works](docs/stateless-renderers.md#metrics-and-logs); the dashboards are in countdown-server.

## Releasing and promoting

Two environments, each one host running [countdown-server](https://github.com/nvorkinn/countdown-server)'s stack (with
the Redis this repository's render pool and providers use) and this repository's containers: **dev-qa** and
**production**. A tag is built once and the same tag is promoted.

- **Release** (dev-qa, no approval): push a `v*` tag on a commit that's on `main`.
  `.github/workflows/release.yml` builds the wheels and images, creates the GitHub release and deploys the render pool and providers to dev-qa
  (`.github/workflows/deploy-renderers.yml`). Starting a release is the decision to deploy it there. A release never
  touches production. The tag is the version: it's stamped into the wheels at build time, so the `version` in
  `pyproject.toml` is a placeholder.
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
