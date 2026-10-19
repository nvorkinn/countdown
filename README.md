# pi-dashboard

[![codecov](https://codecov.io/gh/nvorkinn/pi-dashboard/graph/badge.svg)](https://codecov.io/gh/nvorkinn/pi-dashboard)

An e-paper dashboard: a render pool that composes the screens, and the thin devices that fetch and show them.

- [`countdown/`](countdown/) -- the Python code. Its render pool (`countdown-server`'s workers and ticker) and its
  providers run as Docker containers next to the auth broker, which hands each finished frame to the screen: see
  [`countdown/server/deploy/`](countdown/server/deploy/).

There's no per-device install: the pool and the providers are deployed from CI, and a screen only fetches frames.

## Releasing

Push a `v*` tag on a commit that's on `main`; `.github/workflows/release.yml`
builds the wheels and the images, creates the GitHub release and deploys the render pool and the providers. The tag
is the version of the app -- it's stamped into the wheels at build time, so the
`version` in `countdown/pyproject.toml` is just a placeholder and doesn't need bumping.

The countdown CI workflow only runs when files under its folder change
(`.github/workflows/countdown-ci.yml`).

## Device name

The `DEVICE_NAME` setting names a process that doesn't name itself otherwise (the providers) as `device` on every log
line. It's sanitized (trimmed, lowercased, anything outside `[a-z0-9_-]` becomes `-`, see
`countdown_core/core/device_name.py`), and defaults to the hostname. It isn't a screen's `device_id` on the broker, nor
its slug: the render pool names each screen by its slug in its metrics, and itself as `worker` in its logs.

## The standalone Raspberry Pi mode

The Pi that was its own screen (`countdown-standalone`, with its `install.sh`, self-updater, Fluent Bit setup
and encrypted secrets bundle) has been removed. The last commit that has it is tagged `last-standalone`.
