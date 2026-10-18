# countdown

The long-running Python app that renders the 7.5" e-paper dashboard for a separate screen to fetch:
TfL arrivals, weather, a notice board, Glowmarkt energy use, Spotify and a developer panel, all
configured remotely through [auth-broker](https://github.com/nvorkinn/auth-broker).

This README is about the renderer. For the whole system (the ESP32 screen, Caddy, authentik, the broker, the
orchestrator and the renderers: who calls whom, which token or secret exists when, and what each call changes)
see auth-broker's [How the pieces fit together](https://github.com/nvorkinn/auth-broker/blob/main/docs/architecture.md)
and [The database](https://github.com/nvorkinn/auth-broker/blob/main/docs/database.md).

Where the renderers are heading (a pool of workers drawing each frame as a job, rather than a container per screen)
is decided, with its migration plan, in [Stateless renderers](docs/stateless-renderers.md).

## How it works

**Boot** (`app.py`). The app reads `BROKER_URL` and `CLAIM_TOKEN` (both required; the
orchestrator sets them) and claims its screen with the broker before it does anything
else (`countdown_credentials/registration.py`). On first run it makes up a secret, sends
it with the one-time claim token, which matches it to its screen at once, and saves the
credentials to `.auth_broker_device` in the working directory; later runs just load them
and ignore the token. Then it sets up the display target and waits for a valid config, from the store or else from
the broker (see [The config](#the-config)). The app can't run without that config, so there's no
empty fallback: if neither has one, or the broker sends back something invalid,
it keeps retrying, backing off from 30 seconds to 5 minutes. Nothing is painted
until it has a config. Once it's running, a failed config refresh just keeps the
config it already has.

**The loop** (`display_loop.py`). Every `interval` seconds (the broker's 60; nobody sets it any more), or sooner
while a provider asks for a faster refresh (see [The pace](#the-pace)), the device is in one of two stages:

- *pairing*: the config carries a pairing code, so the device shows it full
  screen. It's only repainted when the code changes.
- *running*: the dashboard.

### The pace

How often the screen refreshes is not a setting: the providers decide. A provider whose data can turn bursty (the
developer provider, while a release runs) writes `v1:pace:<source>:<id>` to the store with a ~1 minute TTL, holding the
seconds it wants (see `countdown_core/store/keys.py`). Each cycle the renderer takes the fastest of those its clients
watch (`AbstractClient.pace_keys`, `ApiRegistry.pace`), sleeps that long (never under 20 s, never over the
config's interval: `DisplayLoop.sleep_s`), and tells the target (`DisplayTarget.set_pace`). The server target sends it
with each frame as `X-Refresh-Hint`, and again with the same frame when the picture hasn't changed (`renew`), so the
broker keeps answering the screen's polls with it as `Retry-After`. When the provider stops asking, the key lapses, the
header stops, and everything is back to the usual within a lease. Providers that can ask define
`pace(item_id, fetched)` (see `countdown_providers/provider.py`).

After each cycle the loop publishes health over OTLP (see
[Metrics](#metrics)) and re-reads the config, from the store or else from the broker, as soon as the broker says it
changed, or at the latest when the cycle's interval is up.

### The config

The screen's config is in the store: auth-broker keeps its database as the source of truth, and writes each screen's
config to `v1:config:<device id>` (`store/keys.py`'s `config`) on every config save and as it starts. The value is
exactly what `GET /api/config` answers the device, the contract's `ConfigResponse`, less its secrets
(`weather.api_key` empty, which the renderer doesn't use, and no `pairing_code`), so the renderer reads it with the
same model (`store/config.py`'s `read`, through `BrokerClient.get_config`). It's self-contained: a fresh process can
draw the screen from it and the providers' keys alone. `tests/fixtures/config_store.json` pins it, the same file as
auth-broker's tests read.

- **The nudge.** After changing a device's key the broker publishes the device id on `v1:config:changed`. The
  `ConfigWatcher` (`store/config.py`), which the app starts with the loop when it has `REDIS_URL`, listens for its
  own id and cuts the loop's wait short, so a save on the settings page is drawn at once rather than at the next
  interval. A nudge missed while Redis was away only delays the change until the next cycle, which reads the key
  anyway.
- **The fallback.** With no key for the device, the renderer asks `GET /api/config` as before: an older broker that
  writes none, a renderer with no store (no `REDIS_URL`), a Redis that doesn't answer, or a stored value that doesn't
  validate (each logged). It's also how the pairing code reaches the screen: the broker keeps no key for a device
  that isn't paired or has a code to show, as only `GET /api/config` gives (and issues) the code. Once the device is
  paired the broker writes the key and nudges, and the renderer moves to the store; the log says each time it moves.
- **The pace.** Nothing in `GET /api/config`'s response headers reaches the renderer, so nothing had to be carried
  over: the base `interval` is in the body, and so in the stored value, and a faster pace comes from the providers'
  `v1:pace:` keys and goes out on the frame upload (see [The pace](#the-pace)), never through the config.

To deploy: auth-broker's side first (it writes keys nothing reads yet), then a release of this; the other order works
too, with the renderers asking the broker until its keys are there. To check the keys, on the host:

```sh
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 --scan --pattern "v1:config:*"'
```

lists one per paired screen; a renderer's log says "Reading the config from the store" once it reads its own.

**Clock format.** The config website's clock choice is the top-level `clock_format` in
`GET /api/config`: `"24h"` (the default when omitted) or `"12h"`. Generated clock times use
`07:21` / `18:04` or `7:21 AM` / `6:04 PM`, including `12:00 AM` at midnight and `12:00 PM`
at noon. This covers portrait weather sunrise/sunset and overnight windows, hourly energy
chart labels, Met Office warning expiry and clock-change reminders (which previously always
used compact `1am` / `2am` text). A change takes effect on the next dashboard frame after the
normal config refresh, including cached panels; it does not restart clients, reset the weather
carousel or change polling. Layouts receive the preference per frame and do not store it.
Times stay in their data source's time zone. Durations (including arrival countdowns), API
timestamps/parameters, logs and upstream free-form notice text are not rewritten.

**API clients** (`*_client.py`, `api_registry.py`). Each API has an
`AbstractClient` subclass: the departures (TfL and Entur), Open-Meteo weather, Glowmarkt energy,
Spotify and Apple Music, the
developer panel (the owner's GitHub pull requests and failing builds, and their AI plans; while one of their repos is
releasing, the release's jobs as GitHub's workflow graph instead: a finished job is a black box with white ink, a
running one has its name in bold, and one waiting for the owner's approval or failed has a heavier outline and a
warning triangle; `developer/release_styles.py`) and the notice board. The departures, weather,
energy, music and developer clients read only the shared Redis store, which the [providers](providers/README.md) (and
auth-broker's, for energy, music and the developer panel) fill: the renderer never calls TfL, Entur, Open-Meteo,
Glowmarkt or GitHub for them, nor the broker, nor the music services' image hosts for the album covers, and never holds
a Glowmarkt password, a Spotify token or a GitHub token. Nor does it use TfL's app key: the providers read
`TFL_APP_KEY` themselves. A
renderer started without `REDIS_URL` can't build those five, so their areas say "Could not connect" and the log says
why (`store.reader.require_store`); a key missing from the store, or Redis not answering, is just a failed poll. The
notice board reads its bank holidays (`v1:notices:bank-holidays`), the Met Office's warnings
(`v1:notices:met-office:<place>`), the Environment Agency's flood warnings (`v1:notices:floods:<place>`), Entur's
disruptions (`v1:notices:entur:<stop id>`) and TfL's disruptions (`v1:notices:tfl-*`) from the store too; its other
sources still call their APIs.
`ApiRegistry` builds a client for every API the config enables.
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
(placed from the store, where `provider-locations` keeps each postcode's place from postcodes.io), and bank holidays
and clock changes. Each source
has its own refresh interval. The board's own sources are about the postcode's place; the disruptions at the stops
come from the departures client, as every client can hand the board notice sources about what it shows
(`AbstractClient.notice_sources`, passed on by the registry). A failing source keeps its last notices for a
while (at least an hour) before they're dropped. Notices are ranked by severity, and planned works only fill rows left
over. The flood warnings come from the shared store (`v1:notices:floods:<place>`), which the floods provider fills from
the Environment Agency; the board makes its notices from what's there as it reads it.

**Layout** (`rendering/`). For every frame, `DisplayController` asks `LAYOUTS` (a `LayoutRegistry`) for a
layout, and keeps nothing about the answer. Layouts are registered by the canvas they draw
on: the screen in the config, as it's hung, so 800x480 landscape or 480x800 portrait. A
canvas with no layouts yet gets the 800x480 ones. Several layouts can share a canvas (the
config will choose one by its `layout_id`; until it can, the canvas's first is used), and
each has a variant for every combination of the APIs available: Glowmarkt is on when the
config's `glowmarkt.enabled` is, and Spotify when it's enabled. The only layout so far,
`classic`, puts the arrivals top-left with the weather beside them. The developer panel has no area of its own: it
shares Spotify's (`rendering.share_spotify_area`), all the time without Spotify, and with it one minute in three,
whenever Spotify has nothing to show, or every minute while it's showing a release. The layout also sets
how many stops are on screen at once (four with neither Spotify nor Glowmarkt, otherwise
two), so it's picked before the clients are polled, and the TfL client pages through the rest.

**Pages turn with the clock** (`core/rotation.py`). Every panel that rotates (the stops, the weather's tabs, the
energy panel's three windows, the Spotify area's player and top lists) shows page *minute since the epoch mod its
number of pages*, worked out when the client is polled. So every screen turns its pages together, on the minute, two
polls in the same minute show the same page, and nothing about the rotation is kept: a client rebuilt by a config
change carries on at the clock's page rather than starting again at the first. When Spotify's page has nothing to
show, the next one is drawn in its place.

**Painting** (`core/display.py`, `core/targets.py`). `DisplayController`
only paints when the picture changes. If only the arrivals changed, it does a
quick partial refresh of their box. Otherwise, or once 10 minutes have passed
since the last full refresh (partial refreshes leave ghosting), it does a full
refresh. If every API only has a message to show, a "Nothing to show yet" screen
replaces the dashboard (`rendering.render`). Frames are drawn upright, as the screen is
hung; a vertical screen's 480x800 frame is turned a quarter into the panel's own 800x480
picture before it's painted (`rendering.to_native`), and the pairing and "Nothing to show
yet" screens are drawn smaller to fit its width. Until a 480x800 layout exists, a vertical
screen's dashboard is still the landscape one. Frames go to a display target: `ServerTarget` hands each one to the broker for
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
- `orchestrator/` (`countdown-orchestrator`): starts a renderer container for each screen (see
  [its README](orchestrator/README.md)).
- `providers/` (`countdown-providers`): one long-running process per API, fetching what screens subscribe to into a
  shared Redis store, which the renderers read; see [its README](providers/README.md).

`uv sync` installs them all, editable, for development and the tests.

### Weather panel

The monochrome weather panel rotates through **Now, Tonight, Tomorrow and Weekend**. A filled pill
marks the selected tab; narrow landscape panels abbreviate the labels. Each time the weather client
is asked to update, it returns the tab for the minute (see "Pages turn with the clock" above), so the
tabs come round once every four minutes. Rendering a frame doesn't change it, and the registry keeps the
current tab while the client is not due for a poll.

The rotation uses a cached eight-day Open-Meteo forecast, which the weather provider stores for the place (under the
config's `weather.key`, see the providers' README, "Weather"), not a read per tab. It's read again every 10 minutes, or when the date changes at the weather location. A failed read (the
forecast not in the store, or Redis not answering) keeps the last good frame; the next good read shows the minute's
tab. Changing the configured location reads the new place's forecast at once, on the minute's tab. With no location
configured the panel says it needs configuring.

Now shows current conditions; landscape panels also show today's low, high and rain probability.
Tonight shows the overnight low from 18:00 to 06:00, excluding hours that have already passed.
Tomorrow shows the daytime high.
Weekend covers the next complete Saturday and Sunday, including the current weekend on Saturday but
the following weekend on Sunday. Its headline is the range of the two daytime highs, with each day's
high, low and icon shown separately on portrait screens. Forecast dates and temperature labels make
these readings distinct from the current temperature.

Portrait layouts retain sunrise, sunset, humidity, wind and the next outlook on Now, and use
period-specific readings on the forecast tabs. Unavailable rain probability is omitted, but a reported
0% is shown; a combined period is unknown if any of its rain readings is missing. Short, wide panels
reflow horizontally rather than shrinking the whole layout. Both orientations retain clear margins.

### Departures

The departures panel (the `tfl` client, by its old name) shows every stop in the broker's `departures`, London's
and Norway's mixed in the owner's order. Each stop names its source: `tfl` stops are asked of TfL by NaPTAN id,
`entur` stops of [Entur's Journey Planner](https://developer.entur.org/pages-journeyplanner-journeyplanner) by NSR
id, a whole stop place (`NSR:StopPlace:58381`) or one platform (`NSR:Quay:7333`), for Ruter and every other
Norwegian operator. Entur needs no key, only the `ET-Client-Name` header. Both are drawn in the same four styles; a
Norwegian platform's code stands where a London bus stop's letter does. A broker that predates `departures` is read
through `tfl` as before.

### Language, time zone and Norway

The broker's `language` (`en` or `nb`) and `timezone` are per screen; a change to either rebuilds every client.
Every text the screen draws is English in the code, marked with `_()` (or `N_()` where it's defined away from where
it's drawn) and translated from `core/src/countdown_core/locale/<language>.po` as it's drawn; an untranslated text
stays English. After adding or changing a text, run `sh dev/update_translations.sh`: it regathers them into
`messages.pot` and brings `nb.po` in line, new texts untranslated. Dates go through Babel with a CLDR pattern
(`i18n.format_date`), so the pattern itself is translatable ("EEE d MMM" in English, "EEE d. MMM" in Norwegian).

A Norwegian postcode (`notice_board.country` is `NO`) gets Norway's notices instead of the UK's: it's placed by
[Kartverket](https://ws.geonorge.no/adresser/v1/) (through the store: `provider-locations` asks it), the public holidays
are worked out here, weather warnings come from [MET Norway's
MetAlerts](https://api.met.no/weatherapi/metalerts/2.0/documentation) in the screen's language (through the store:
`provider-metalerts` fetches them for all of Norway, and the board picks out its place's, see `providers/README.md`),
and the clocks-change reminder goes by the screen's time zone. Entur stops bring their disruptions to the board whatever
the postcode, as TfL stops bring theirs.

## The API contract

The API between the renderer and [auth-broker](https://github.com/nvorkinn/auth-broker) is defined in neither
repository but in [countdown-api](https://github.com/nvorkinn/countdown-api): an OpenAPI file and a Python package
with Pydantic models, a `requests` client for this side, and Flask views for the broker's. So far the contract
covers `GET /api/config`; the other calls (registering, claiming, frames, the orchestrator's) are
ordinary `requests` code here.

Here it is a git submodule and a member of the uv workspace:

- `countdown/api` is the submodule (`.gitmodules`), and `api` is a workspace member (`pyproject.toml`:
  `countdown-api = { workspace = true }`), so `uv sync` installs it editable like the other packages and `uv.lock`
  records it as a path, with no version or hash. `core/pyproject.toml` depends on `countdown-api` with no version.
- To move to a newer contract, move the submodule and commit the new pointer (nothing does it for you here):

  ```bash
  git submodule update --remote countdown/api   # from the repository root
  git add countdown/api && git commit
  ```

- The recorded commit is what a fresh checkout and local development get. **CI and the release workflow don't
  use it:** they run `git submodule update --init --remote` first, so they test and build countdown-api's
  current `main`.
- A release stamps its version on every workspace package (`uv version`), countdown-api included, and
  `uv build --wheel --all-packages` builds the `countdown_api` wheel from the submodule into `dist/`. The server image
  installs it from there: our packages only ever come from these wheels, never an index. So the wheel carries the
  release's version, not countdown-api's own.
- countdown-api also publishes each of its releases as a package index (https://nvorkinn.github.io/countdown-api/),
  for projects that want a pinned version. This repository doesn't use it.
- `BrokerClient.get_config` reads the broker's copy of the config from the store, and otherwise
  (`BrokerClient.fetch_config`) calls `countdown_api.clients.ApiConfigClient`, through the device's session, so the
  path, the method, the headers and the shape of the response all come from the contract.
- The app reads the contract's own `ConfigResponse` (and its sections) as `get_config` returns it, whichever way it
  came, with no model of its own beside it, so the two can't diverge. A response that doesn't match the contract (a
  section missing, an `interval` that isn't positive) is rejected, and the renderer keeps the config it has.
  `tests/test_broker_contract.py` fails if the contract (the package carries `countdown_api/openapi.yaml`) and the
  client stop agreeing on the path, the security and the headers the app sends.

What the client accepts is the compatibility policy: the broker may **add** fields (the client ignores
ones it doesn't know), but a value outside what the contract lists (a third screen orientation, say) is invalid, the
config is rejected, and the renderer keeps the one it has. A test pins each half.

## Running it in development

In production the orchestrator makes the renderer, inside a container, with a one-time claim token, so there's
nothing to attach a debugger to. In development you start it yourself, as a plain process (from PyCharm, with the
debugger, or from VS Code).

A screen that's already set up (WiFi, account, config) keeps all of that if you **take over its existing renderer**
rather than make a new one. That's the quick way:

```bash
DOCKER_HOST=ssh://user@host uv run python dev/adopt_renderer.py <slug>   # copies its identity and key, writes dev/.env
```

Then, on the host, stop the orchestrator (it restarts a stopped renderer within seconds) and the renderer
(`docker stop <orchestrator> renderer-<slug>`), so two never run as one device, and start yours. Nothing changes on
the broker and no tokens are needed. When you're done, start both again. The screen (the ESP32) needs nothing at all.

On a **local broker** there is nothing to take over, so seed one: it writes a paired screen, its config and a
provisioned renderer straight into the broker's SQLite (stop the broker, and run its migrations first by starting
it once; it only knows the schema as of alembic revision 0009):

```bash
uv run python dev/seed_broker.py /path/to/broker.db --owner <your user id> --slug dev --weather London --stops <naptan>
```

It writes identities for both processes, `dev/.env`, and prints nothing secret (the secrets live only in the
encrypted identity files, beside their keys). The seeded renderer and screen are already paired, so start the renderer and it just works; the broker must speak
the contract in `api/` (a broker newer or older than the submodule's pin can reject the renderer or the config).
`--replace` re-seeds a slug.

All three scripts (`adopt_renderer.py`, `seed_broker.py` and `dev_env.py`) also write a **PyCharm run configuration**,
"countdown-server (<slug>)", to `.idea/runConfigurations/` (git-ignored): module `countdown_server.main`,
working directory `countdown/dev/renderer`, and the environment inline. Pick the project's uv interpreter once in it
(`countdown/.venv`) and press Debug. If your PyCharm project isn't this checkout (a worktree, say), run the script
with `--idea-dir /path/to/project/.idea`; `--no-pycharm` skips it. It's regenerated each run, so the variables stay
current.

To make a **new** renderer instead (a screen with none yet, or a local broker):

1. **Use a broker with no orchestrator watching it** (a local auth-broker, or stop the orchestrator on the host
   for now). Otherwise it starts a container for the assignment within 10 seconds and claims the screen before
   you do. A screen that a renderer has already claimed has no claim token left, so take that renderer over (above).
2. On the broker's config page, set up a screen and pick a slug for its renderer.
3. Write the environment both processes use (the claim token, the slug and `BROKER_URL`) to `dev/.env`, which is
   git-ignored:

   ```bash
   BROKER_URL=http://localhost:5000 ORCHESTRATOR_TOKEN=<its token> uv run python dev/dev_env.py <slug>
   ```

Then:

4. **Renderer, in PyCharm.** Run the generated configuration, or make a Python one by hand with module `countdown_server.main`, working directory
   `countdown/dev/renderer`, the project's uv interpreter and `countdown/dev/.env` under "Paths to .env files".
   Run it with Debug. It claims the screen the first time, and keeps its identity in
   `dev/renderer/.auth_broker_device`, so later runs reuse it (the stale `CLAIM_TOKEN` is ignored).

The VS Code "Python Debugger: countdown-server" configuration runs the renderer the same way, if you'd rather
debug it there. `LOG_LEVEL` in `dev/.env` is `DEBUG`; the default is `INFO`. Without a Fluent Bit on
`127.0.0.1:4318` the renderer logs a metrics warning each minute, which is expected.

To start over with a new renderer, delete `dev/renderer/.auth_broker_device` and its assignment, and make a new one:
the claim token is single-use.

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

There's no local config file. A screen registers itself with
[auth-broker](https://github.com/nvorkinn/auth-broker); its owner pairs it at
https://nikolaivorkinn.com and picks a name (the slug) for its renderer; the
[orchestrator](orchestrator/README.md) then starts this renderer for it, with a one-time
claim token. From then on everything is managed on the broker's pages: TfL stops,
weather location, Glowmarkt energy account, and connecting Spotify (the broker
handles the OAuth handshake and its music provider fetches now-playing into the store, so the renderer never holds a
Spotify token itself). The renderer's own settings are `BROKER_URL`, `CLAIM_TOKEN`, `DEVICE_NAME`, and `REDIS_URL`
and `REDIS_PASSWORD` for the store (the orchestrator passes those); everything else comes from the broker. (The
broker sends only `glowmarkt.enabled` for the energy panel, never the Glowmarkt credentials.)

Once paired, an API that still needs setting up shows "Not configured" in its area (see
[How it works](#how-it-works)).

## Logs

The renderer and the orchestrator log one JSON object per line on stderr (`countdown_core/core/log_format.py`,
and the orchestrator's own copy): `level` (`debug`, `info`, `warn`, `error`, `fatal`), `_msg`, `logger`,
`source` (file:line), and `exception` when there is a traceback, kept inside its record. The renderer adds
`device` (its slug). There is no timestamp: the container's log driver stamps each line. `LOG_LEVEL` sets the
level (default INFO); `asyncio`, `PIL` and `opentelemetry` stay at WARNING, and the renderer's and the providers'
`urllib3` is off (the orchestrator's stays at WARNING), as its retry warnings and request lines quote the URLs it asks.

Nothing a renderer or provider logs may say where a screen is: no postcode, place, district or coordinates, nor a URL
with one in it, such as a failed request's text (`log_format.error_reason` logs its type and status instead).

The host's Fluent Bit ships the containers' json-file logs to VictoriaLogs, naming each stream after the
container's `tag` log option: the orchestrator's compose file sets `orchestrator`, and the orchestrator
makes each renderer with `tag: {{.Name}}`, so its stream is `renderer-<slug>`. A renderer made before this
carries no tag (its stream is the container id) until the next release recreates it. The dashboards are in
auth-broker (`docs/architecture.md`).

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
