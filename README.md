# pi-dashboard

[![codecov](https://codecov.io/gh/nvorkinn/pi-dashboard/graph/badge.svg)](https://codecov.io/gh/nvorkinn/pi-dashboard)

Draws the screens of an e-paper dashboard. ESP32 screens poll [auth-broker](https://github.com/nvorkinn/auth-broker)
for frames; this repository's render pool draws every screen from its config and panel data in Redis and writes the
frame back to Redis, and its providers fetch the panel data.

- [`countdown/`](countdown/) is the Python code: how it works, and the render pool and providers in it.
- [`countdown/server/deploy/`](countdown/server/deploy/) is how both run, as Docker containers next to the broker.

## Releasing

Push a `v*` tag on a commit that's on `main`. `.github/workflows/release.yml` builds the wheels and images, creates the
GitHub release and deploys. The tag is the version: it's stamped into the wheels at build time, so the `version` in
`countdown/pyproject.toml` is a placeholder.

`.github/workflows/countdown-ci.yml` runs only when files under `countdown/` change.
