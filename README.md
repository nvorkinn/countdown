# pi-dashboard

[![codecov](https://codecov.io/gh/nvorkinn/pi-dashboard/graph/badge.svg)](https://codecov.io/gh/nvorkinn/pi-dashboard)

An e-paper dashboard: a renderer that composes the screens, and the thin devices that fetch and show them.

- [`countdown/`](countdown/) -- the Python code. Its renderer (`countdown-server`) runs as a Docker container
  next to the auth broker, which hands each finished frame to the screen: see
  [`countdown/server/deploy/`](countdown/server/deploy/).

There's no per-device install: the renderers are deployed from CI, and a screen only fetches frames.

## Releasing

Push a `v*` tag on a commit that's on `main`; `.github/workflows/release.yml`
builds the wheels and the server image, creates the GitHub release and deploys the renderers. The tag
is the version of the app -- it's stamped into the wheels at build time, so the
`version` in `countdown/pyproject.toml` is just a placeholder and doesn't need bumping.

The countdown CI workflow only runs when files under its folder change
(`.github/workflows/countdown-ci.yml`).

## Device name

The `DEVICE_NAME` setting names a renderer in its metrics (as `host.name`) and in the `X-Device-Name` header it sends
the broker. It's sanitized (trimmed, lowercased, anything outside `[a-z0-9_-]` becomes `-`, see
`countdown_credentials/device_name.py`), and defaults to the hostname. It isn't the `device_id` the auth broker gives a
renderer when it registers.

## The standalone Raspberry Pi mode

The Pi that was its own screen (`countdown-standalone`, with its `install.sh`, self-updater, Fluent Bit setup
and encrypted secrets bundle) has been removed. The last commit that has it is tagged `last-standalone`.
