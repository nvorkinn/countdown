# countdown

The long-running Python app that renders the 7.5" e-paper dashboard for a separate screen to fetch:
TfL arrivals, weather, a notice board, Glowmarkt energy use and Spotify, all
configured remotely through [auth-broker](https://github.com/nvorkinn/auth-broker).

## How it works

**Boot** (`app.py`). The app reads `BROKER_URL` and registers with the broker
before it does anything else (`countdown_credentials/registration.py`). On first
run that saves its credentials to `.auth_broker_device` in the working directory;
later runs just load them. The renderer then waits there until a screen has been
matched with it. Then it sets up the display target and waits for a valid config. The app can't run without that config, so there's no
empty fallback: if the broker can't be reached or sends back something invalid,
it keeps retrying, backing off from 30 seconds to 5 minutes. Nothing is painted
until it has a config. Once it's running, a failed config refresh just keeps the
config it already has.

**The loop** (`display_loop.py`). Every `interval` seconds (set in the config),
the device is in one of three stages:

- *pairing*: the config carries a pairing code, so the device shows it full
  screen. It's only repainted when the code changes.
- *setup*: paired, but `setup_missing` isn't empty (it needs a weather location
  and at least one bus or tube stop). A "You're paired!" checklist is shown and
  no API is polled.
- *running*: the dashboard.

After each cycle the loop publishes health over OTLP (see
[Metrics](#metrics)) and re-reads the config from the
broker. The broker has no change notification, so this poll is how changes to
stops, credentials and so on reach the device, and how the pairing code stays
current.

**API clients** (`*_client.py`, `api_registry.py`). Each API has an
`AbstractClient` subclass: TfL arrivals, Open-Meteo weather, Glowmarkt energy,
Spotify (proxied by the broker, so the device never holds a Spotify token) and
the notice board. `ApiRegistry` builds a client for every API the config enables.
When the config changes, it only rebuilds the clients whose `needs_refresh()`
says the change affects them. Each client has its own `poll_interval`. The
clients that are due are polled concurrently, with their blocking `requests`
calls running in worker threads, so a cycle takes as long as the slowest API.
A client that isn't due, or whose poll failed, keeps its last panel. An API
with no panel at all gets a `MessagePanel` ("Not configured", "Could not
connect"). A client that failed to initialise tries again on its next poll, and
one broken API never takes the others down.

**Notice board** (`notice_board_client.py`, `notices/`). This covers TfL line
status and station disruptions for the configured stops, Met Office warnings,
Environment Agency flood alerts and TfL road incidents near the owner's postcode
(looked up with postcodes.io), and bank holidays and clock changes. Each source
has its own refresh interval. A failing source keeps its last notices for a
while (at least an hour) before they're dropped. Notices are ranked by severity, and planned works only fill rows left
over.

**Layout** (`display_composers/`). Which APIs are available decides the layout:
Glowmarkt is on only when the config has both its credentials, and Spotify when
it's enabled. There's one composer for each combination of the two. Every layout
puts the arrivals top-left with the weather beside them. The layout also sets
how many stops are on screen at once (four with neither Spotify nor Glowmarkt,
otherwise two), and the TfL client pages through the rest.

**Painting** (`core/display.py`, `core/targets.py`). `DisplayController`
only paints when the picture changes. If only the arrivals changed, it does a
quick partial refresh of their box. Otherwise, or once 10 minutes have passed
since the last full refresh (partial refreshes leave ghosting), it does a full
refresh. If every API only has a message to show, a "Nothing to show yet" screen
replaces the dashboard. Frames go to a display target: `ServerTarget` hands each one to the broker for
the screen to fetch. If the target can't take a frame, the app carries on, and a
whole-screen picture it couldn't paint (pairing code, checklist) is retried
every cycle until it can.

## Packages

This directory is a uv workspace:

- `core/` (`countdown-core`): the app itself: clients, panels, layouts and the
  display loop, with no e-paper hardware dependencies. A library only: it has
  no script of its own, and the apps depend on it.
- `credentials/` (`countdown-credentials`): registering with the broker and
  keeping the device's credentials encrypted at rest.
- `server/` (`countdown-server`): the renderer. It runs as a Docker container
  next to the broker (see [`server/deploy/`](server/deploy/)) and sends each
  frame to the broker.
- `client/` (`countdown-client`) and `epd/` (`countdown-epd`): the thin screen
  that fetches finished frames and paints them on the panel, with the vendored
  Waveshare driver.

`uv sync` installs them all, editable, for development and the tests.

## The broker's API contract

The config the renderer polls from [auth-broker](https://github.com/nvorkinn/auth-broker) is defined there, in
pydantic (`broker/contract.py`), and published as `openapi.json`. Three things here follow it:

- `core/contract/broker-openapi.json` is a copy of that file.
- `config_server/broker_api/` is the client for it: Pydantic models and a `requests` client, checked in as-is.
  `BrokerClient.get_config` calls it, through the device's session, so the path, the method, the headers and
  the shape of the response come from the contract.
- `AppConfig` is what the app actually reads of the response.

`tests/test_broker_contract.py` fails if the copy, the generated client or `AppConfig` stop agreeing. To pick up a
change to the broker's API, copy in the new `openapi.json`, update `broker_api/` to match, and run:

```bash
cp ../auth-broker/openapi.json core/contract/broker-openapi.json
uv run pytest tests/test_broker_contract.py tests/test_broker_client.py
```

A new field the broker sends fails the tests until `AppConfig` reads it (or it's listed as unused), so the two
can't quietly diverge.

What the generated client accepts is the compatibility policy: the broker may **add** fields (the client ignores
ones it doesn't know), but a value outside what the contract lists (a third screen orientation, say) is invalid, the
config is rejected, and the renderer keeps the one it has. A test pins each half.

## Running locally

```bash
BROKER_URL=https://auth.nikolaivorkinn.com uv run countdown-server
```

It registers as a renderer and waits for a screen to be matched with it.
`LOG_LEVEL` (e.g. `DEBUG`) overrides the default `INFO`.

## Visual Regression Testing

The rendered e-paper screen is tested by comparing it against golden PNGs
checked into `tests/images/`, rather than only asserting on the data that
feeds it. `tests/test_display_snapshots.py` builds each scenario (arrivals,
energy, weather, Spotify now-playing, empty states, etc.) directly from
representative data and renders it through the real `DisplayController`.

### Running it

```bash
pytest tests/test_display_snapshots.py
```

Compares each scenario's render against its golden image in `tests/images/`.
On a mismatch, the test fails with the actual render and a red-highlighted
diff written to `tests/images/_failures/<name>.actual.png` /
`<name>.diff.png` so you can see exactly what changed.

### Updating goldens after an intentional change

```bash
pytest tests/test_display_snapshots.py --update-snapshots
```

This only rewrites the golden images for scenarios whose render actually
changed -- `git status` afterwards shows exactly which ones. Review the new
PNGs, then commit them alongside your code change.

### Reviewing in a PR

Since the goldens are just PNGs committed to the repo, GitHub's own "Files
changed" view renders a visual diff (2-up / swipe / onion-skin) for any
changed image automatically -- no extra tooling needed. The loop is:
change code -> run tests (see what broke) -> `--update-snapshots` if the new
output is correct -> commit the changed PNGs -> open a PR -> review the
image diffs on GitHub -> merge.

### Adding a new scenario

Add a function to `tests/test_display_snapshots.py` that builds the panels
it needs and calls `snapshot.assert_matches("some_name", image)`. Run with
`--update-snapshots` once to create its golden image, review it, and commit
it.

## Deploying

Each tagged release builds the wheels and the `countdown-server` Docker image,
and deploys the renderers (see `.github/workflows/release.yml` at the repo root
and [`server/deploy/`](server/deploy/)).

There's no local config file: on first boot the renderer registers itself with
[auth-broker](https://github.com/nvorkinn/auth-broker), and the screen shows a
pairing code. Go to https://nikolaivorkinn.com, enter the code, and manage
everything from there: TfL stops, refresh interval, weather location, Glowmarkt
energy account, and connecting Spotify (the broker handles the OAuth handshake
and proxies now-playing calls, so the renderer never holds a Spotify token
itself). The only local setting is `BROKER_URL`; everything else, including
Glowmarkt credentials, comes from the broker.

Once paired, the screen shows a checklist of anything still missing (see
[How it works](#how-it-works)).

## Metrics

The renderer reports its health as OTLP/HTTP metrics (protobuf, via the
OpenTelemetry Python SDK) to the host's Fluent Bit OpenTelemetry input at
`http://127.0.0.1:4318/v1/metrics`, tagged with `host.name` (the `DEVICE_NAME`
setting, else the hostname). Gauges:

- `countdown.api.status`: 1 per API, with `api` and `status` attributes
- `countdown.problem`: 1 while any API is in `error` or `fatal`
- `countdown.stage`: 1, with a `stage` attribute
- `countdown.display.connected`: whether the screen is connected
- `countdown.broker.last_sync`: Unix time of the last broker sync (absent until the first)

It reports from the moment the app starts, from its own background task, so a
renderer still registering with the broker shows up (as `waiting_for_broker`).
Metrics are sent once a minute, and straight away when the stage changes. A
failed send is logged and retried at the next interval.
