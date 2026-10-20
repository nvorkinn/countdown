# countdown

The Python code that draws the 7.5" e-paper dashboard: TfL and Entur departures, weather, a notice board, Glowmarkt
energy use, Spotify and a developer panel, all configured through [auth-broker](https://github.com/nvorkinn/auth-broker).
ESP32 screens fetch the frames from the broker; nothing here talks to a screen or to the broker.

This repository and auth-broker share only Redis. For the whole system (who calls whom, which secret exists when) see
auth-broker's [How the pieces fit together](https://github.com/nvorkinn/auth-broker/blob/main/docs/architecture.md).

## Packages

A uv workspace; `uv sync` installs everything editable.

- `core/` (`countdown-core`): the drawing: API clients that read the store, panels, layouts and the render function.
- `server/` (`countdown-server`): the render pool, `countdown-render-worker` and `countdown-render-ticker`; see
  [How rendering works](docs/stateless-renderers.md).
- `providers/` (`countdown-providers`): one process per API, fetching what screens subscribe to into the store; see
  [its README](providers/README.md).
- `api/`: the [countdown-api](https://github.com/nvorkinn/countdown-api#readme) submodule, the store contract.

Running them is in [`server/deploy/`](server/deploy/).

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
read only the store, which the [providers](providers/README.md) and auth-broker's providers fill: the render calls no
upstream API and holds no API token. A client whose key has expired returns a "Not available" panel; one that fails
for another reason shows "Could not connect", and an API that isn't set up shows "Not configured". Redis not answering
fails the render. One broken API never takes the others down.

**Notice board** (`notice_board_client.py`, `notices/`). Reads TfL line and station status, Met Office warnings,
Environment Agency flood warnings, TfL road incidents, bank holidays and clock changes from the store, plus Entur
disruptions and, for a Norwegian postcode, MET Norway's warnings. Where a postcode is comes from `v1:location:<place>`
(`provider-locations`). Notices are ranked by severity, and planned works only fill leftover rows. An expired source
adds nothing, and if every source has nothing the board says "Not available" rather than "All clear".

**Layout** (`rendering/`). `LAYOUTS` holds layouts by canvas (800x480 landscape or 480x800 portrait, as the screen is
hung), each with a variant per combination of enabled APIs. The developer panel has no area of its own
(`place_developer_panel`): while Spotify shows a song it's a strip above it (open PRs and builds in one row, or a
release's jobs in a row of boxes), and otherwise it takes Spotify's whole area.

**The frame.** Frames are drawn upright; a portrait frame is turned a quarter into the panel's native 800x480
(`rendering.to_native`), then packed into its raw 1-bit buffer (`rendering.screen.panel_bytes`). If every API only has a
message to show, a "Nothing to show yet" screen replaces the dashboard. Partial refresh is the firmware's business: the
server always sends the whole frame.

**Language, time zone and clock.** The config's `language` (`en` or `nb`), `timezone` and `clock_format` (`24h` or
`12h`) apply per screen. Every drawn text is English in the code, marked `_()` (or `N_()`), and translated from
`core/src/countdown_core/locale/<language>.po`; an untranslated text stays English. After adding or changing a text,
run `sh dev/update_translations.sh` from `countdown/`. Dates go through Babel (`i18n.format_date`). A Norwegian
postcode (`notice_board.country` is `NO`) gets Norway's notices: public holidays are worked out here and the rest comes
from the store.

## The store contract

The keys, models, fixtures and the enqueue script both repositories use are
[countdown-api](https://github.com/nvorkinn/countdown-api#readme): its README lists every key. `store/keys.py`
re-exports them beside the keys only this repository uses, and `tests/test_store_contract.py` round-trips this side's
readers and writers through the shared fixtures. The broker may add fields (ignored), but a value outside what the
contract lists is invalid and fails the render.

`countdown/api` is a git submodule and a uv workspace member. To move to a newer contract:

```sh
git submodule update --remote countdown/api   # from the repository root
git add countdown/api && git commit
```

A fresh checkout gets the recorded commit. CI and the release run `git submodule update --init --remote` first, so they
use countdown-api's `main`. A release stamps its version on every workspace package, countdown-api included, and the
images install only these wheels, never an index.

## Running a worker in development

Everything a worker draws is in Redis, so point it at a Redis that the broker and providers fill (a local auth-broker,
and the providers). Put the compose services' environment in `dev/.env`:

```bash
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_PASSWORD=<the local Redis's password>
WORKER_NAME=render-worker-dev
LOG_LEVEL=DEBUG
```

Run `uv run --env-file dev/.env countdown-render-worker` from `countdown/`, with `countdown-render-ticker` beside it or
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

The host's Fluent Bit ships the containers' logs to VictoriaLogs, naming each stream after the compose service
(`render-worker-1`, `provider-tfl`...). Workers send OTLP metrics to `http://127.0.0.1:4318/v1/metrics`, listed in
[How rendering works](docs/stateless-renderers.md#metrics-and-logs); the dashboards are in auth-broker.
