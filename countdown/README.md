# countdown

The Python code that renders the 7.5" e-paper dashboard for a separate screen to fetch:
TfL arrivals, weather, a notice board, Glowmarkt energy use, Spotify and a developer panel, all
configured remotely through [auth-broker](https://github.com/nvorkinn/auth-broker).

This README is about the render pool and what it draws. For the whole system (the ESP32 screen, Caddy, authentik, the
broker, the providers and the render pool: who calls whom, which token or secret exists when, and what each call changes)
see auth-broker's [How the pieces fit together](https://github.com/nvorkinn/auth-broker/blob/main/docs/architecture.md)
and [The database](https://github.com/nvorkinn/auth-broker/blob/main/docs/database.md).

How it got here (a pool of workers drawing each frame as a job, where there was a container per screen) is in
[Stateless renderers](docs/stateless-renderers.md), with the migration that did it.

## How it works

**The render** (`render.py`). Every frame is one call of `render(device_id, now)`, which runs to completion: it reads
the screen's config, sets its language and time zone, builds the clients fresh, has them read their keys, draws the
frame as of `now`, hashes it, writes it to the screen's record in the store if the hash changed (see "The frame
record" below), and returns what happened (an `Outcome`: the frame, whether it was written, the
clients' statuses, the panels drawn "Not available"). It has no sleeps, no loops beyond its panels and starts no
threads, and it keeps nothing between calls but the read-only caches (fonts, translations, the Redis clients). Every
"now" a panel goes by (the page it shows, the minutes to a departure, the energy panel's windows, which notices are in
force, whose turn the Spotify area is) is the render's `now` (`core/moment.py`), so the same inputs draw the same
frame. While the broker has a pairing code for the screen, the frame is the pairing screen, drawn full screen (see
[The config](#the-config)).

**The render pool** (`server/src/countdown_server/render_pool.py`; [Stateless renderers](docs/stateless-renderers.md)).
It draws every screen auth-broker lists in `v1:render:screens` (every paired device):

- **The ticker** (`countdown-render-ticker`) queues every screen in `v1:render:screens` on every minute, on the minute.
- **The broker** queues a screen when its config is saved or it issues a pairing code, and a provider whose data turns
  bursty (auth-broker's developer provider, while a release runs) queues its screens on each cycle.
- **The workers** (`countdown-render-worker`, two of them) take jobs off the queue (`store/render_queue.py`: three
  atomic Lua scripts, so one render per screen is in flight at a time and a job that comes mid-render leaves exactly
  one follow-up), and call `render(device_id, now)`. One render at a time per process, each within 20 s (past it,
  faulthandler dumps where it was stuck and ends the worker, and compose restarts it).
- **When the store can't be read**, the render fails and writes nothing, so the screen keeps the frame it has (no
  "could not connect" frame). A key that has expired still draws "Not available".
- A job for a screen not in `v1:render:screens` is dropped. Both run from the `countdown-server` image, each with its
  own entrypoint (`server/deploy/docker-compose.yml`).

### The pace

How often the screen polls is the broker's business alone: auth-broker's pacing function (`services/pace.py`,
`sleep_s`) works out every `Retry-After` from the server's clock, polling twice a minute just after each render
boundary, and faster while a provider asks. A provider whose data can turn bursty (the developer provider, while a
release runs) writes `v1:pace:<source>:<id>` to the store with a ~1 minute TTL, holding the seconds it wants (see
`countdown_core/store/keys.py`), which the broker reads, and queues a render of each of its screens on every cycle, so
there's a new frame to fetch. The render side computes no pace at all. Providers that can ask define
`pace(item_id, fetched)` (see `countdown_providers/provider.py`).

### The config

The screen's config is in the store: auth-broker keeps its database as the source of truth, and writes each screen's
config to `v1:config:<device id>` (`store/keys.py`'s `config`) on every config save and as it starts, and queues a
render of the screen. The value is the contract's `ConfigResponse`, less its `pairing_code` (and it carries no API key),
so the render reads it with the same model (`store/config.py`'s `read`). It's self-contained: a fresh process can draw
the screen from it and the providers' keys alone. The store contract's `config_store.json` (countdown-api's
`countdown_api.fixtures`) pins it, the same file as auth-broker's tests read. A config that doesn't validate (a section missing, a value the contract doesn't list) fails
the render, and the screen keeps its frame; a field the broker adds is ignored.

- **The pairing code.** While the broker has a code for the screen to show, it writes `v1:pairing:<device id>`: the
  config, code included, expiring with the code (`store/pairing.py`), and queues a render. While it's there the render
  draws the pairing screen from it; once it goes (used, lapsed, the device paired) the render draws from
  `v1:config:<device id>` again. The contract's `pairing_key.json` pins it.
- **The interval.** The config's `interval` is in the body, and so in the stored value, but nothing goes by it: the
  screens are drawn on the minute, and the broker paces the polls.

To check the keys, on the host:

```sh
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 --scan --pattern "v1:config:*"'
```

lists one per paired screen.

**Clock format.** The config website's clock choice is the top-level `clock_format` in
the config: `"24h"` (the default when omitted) or `"12h"`. Generated clock times use
`07:21` / `18:04` or `7:21 AM` / `6:04 PM`, including `12:00 AM` at midnight and `12:00 PM`
at noon. This covers portrait weather sunrise/sunset and overnight windows, hourly energy
chart labels, Met Office warning expiry and clock-change reminders (which previously always
used compact `1am` / `2am` text). A change takes effect on the next frame: every render draws its panels
afresh with the config's choice, and it doesn't move the weather carousel, which goes by the clock.
Layouts receive the preference per frame and do not store it.
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
`TFL_APP_KEY` themselves. Each provider's TTL is the only expiry: a key missing from the store (expired, as
its provider is down, or not written yet) makes the panel say "Not available", while Redis not answering fails the
render. The
notice board reads its bank holidays (`v1:notices:bank-holidays`), the Met Office's warnings
(`v1:notices:met-office:<place>`), the Environment Agency's flood warnings (`v1:notices:floods:<place>`), Entur's
disruptions (`v1:notices:entur:<stop id>`) and TfL's disruptions (`v1:notices:tfl-*`) from the store too; its other
sources still call their APIs.
Each render builds an `ApiRegistry` afresh: a client for every API the config enables, each read once, one after
another, in the render's own thread (`AbstractClient.poll`), and all of them dropped when the render ends. A client
whose key in the store has expired returns a `MessagePanel` saying "Not available" (and the render counts it in
`Outcome.expired`), so nothing older than its provider's TTL is drawn. One whose read failed because Redis didn't
answer fails the render (the screen keeps its frame). An API with no panel at all (switched off, or its client hit a
bug) gets a `MessagePanel` ("Not configured", "Could not connect"), and one broken API never takes the others down.

**Notice board** (`notice_board_client.py`, `notices/`). This covers TfL line
status and station disruptions for the configured stops, Met Office warnings,
Environment Agency flood alerts and TfL road incidents near the owner's postcode
(placed from the store, where `provider-locations` keeps each postcode's place from postcodes.io), and bank holidays
and clock changes. Every source
is read on every poll: they're all reads of the store or sums worked out on the spot. The board's own sources are
about the postcode's place; the disruptions at the stops come from the departures client, as every client can hand the
board notice sources about what it shows (`AbstractClient.notice_sources`, passed on by the registry). Nothing a
source said before is kept: one whose key has expired adds nothing, and if every source that reads the store has
nothing, the board says "Not available" rather than "All clear"; Redis not answering fails the render. Notices are
ranked by severity, and planned works only fill rows left over. The flood warnings come from the shared store
(`v1:notices:floods:<place>`), which the floods provider fills from the Environment Agency; the board makes its notices
from what's there as it reads it.

**Layout** (`rendering/`). For every frame, the render asks `LAYOUTS` (a `LayoutRegistry`) for a
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
number of pages*, worked out from the render's `now`. So every screen turns its pages together, on the minute, two
renders in the same minute show the same page, and nothing about the rotation is kept: a config change carries on at
the clock's page rather than starting again at the first. When Spotify's page has nothing to
show, the next one is drawn in its place.

**The frame** (`rendering/`). If every API only has a message to show, a "Nothing to show yet" screen replaces the
dashboard (`rendering.render`). Frames are drawn upright, as the screen is hung; a vertical screen's 480x800 frame is
turned a quarter into the panel's own 800x480 picture (`rendering.to_native`), and the pairing and "Nothing to show
yet" screens are drawn smaller to fit its width. Until a 480x800 layout exists, a vertical screen's dashboard is still
the landscape one. The panel's picture is turned into its raw 1-bit buffer (`rendering.screen.panel_bytes`), which is
the frame. Partial refresh is the firmware's business, if it ever does it: the server always sends the whole frame.

**The frame record** (`store/frame_record.py`). Frames reach the broker only through Redis: the render writes the
picture to the screen's record in the store, `v1:frame:<device id>`, and the broker serves it to the screen. The record
is written only when the frame's hash isn't the record's `etag` (one small read, and no write for an unchanged
picture), and checked again by every render, so a record that was lost (Redis restarted, or a day of the same picture
outlived its TTL) is put back within the minute. Redis not taking the write is logged by the error's type (the
render's result is `unrecorded`, counted as `failed`). The store contract's `frame_record.json` and
`frame_record.bin` pin the record; auth-broker's tests read the same files. To check the records, on the host:

```sh
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 --scan --pattern "v1:frame:*"'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 HMGET v1:frame:<device id> etag rendered_at'
```

lists one per screen the pool draws, each with a TTL under a day (`TTL v1:frame:<device id>`).

## Packages

This directory is a uv workspace:

- `core/` (`countdown-core`): the drawing itself: the clients reading the store, panels, layouts and the render
  function, with no e-paper hardware dependencies. A library only: it has no script of its own, and the apps depend
  on it.
- `server/` (`countdown-server`): the render pool, `countdown-render-worker` and `countdown-render-ticker`. They run
  as Docker containers next to the broker (see [`server/deploy/`](server/deploy/)) and write each frame to the store.
- `providers/` (`countdown-providers`): one long-running process per API, fetching what screens subscribe to into a
  shared Redis store, which the renderers read; see [its README](providers/README.md).

`uv sync` installs them all, editable, for development and the tests.

### Weather panel

The monochrome weather panel rotates through **Now, Tonight, Tomorrow and Weekend**. A filled pill
marks the selected tab; narrow landscape panels abbreviate the labels. Each time the weather client
is asked to update, it returns the tab for the minute (see "Pages turn with the clock" above), so the
tabs come round once every four minutes. Two renders in the same minute draw the same tab.

The rotation uses the eight-day Open-Meteo forecast the weather provider stores for the place (under the config's
`weather.key`, see the providers' README, "Weather"), read on every poll, so nothing of it is kept. Once its key has
expired (the provider down for its TTL) the panel says "Not available"; Redis not answering keeps the last good frame.
The next good read shows the minute's tab. Changing the configured location reads the new place's forecast at once, on
the minute's tab. With no location configured the panel says it needs configuring.

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

## The store contract

This repository and auth-broker share only Redis, and what crosses between them is defined in neither but in
[countdown-api](https://github.com/nvorkinn/countdown-api), the store contract: `store.yaml` and the Python package
generated from it. `countdown_api.keys` names every key that crosses (`store/keys.py` re-exports them beside the keys
only this repository uses), `countdown_api.models` has a model of each value (the config is read with its own
`ConfigResponse` and its sections, with no model of its own beside it, so the two can't diverge),
`countdown_api.scripts` has the enqueue script both sides run (`store/render_queue.py`), and `countdown_api.fixtures`
the canonical examples both sides' tests read (`tests/test_store_contract.py` round-trips this side's readers and
writers through them). Nothing here calls the broker over HTTP.

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
  `uv build --wheel --all-packages` builds the `countdown_api` wheel from the submodule into `dist/`. The images
  install it from there: our packages only ever come from these wheels, never an index. So the wheel carries the
  release's version, not countdown-api's own.

What the render accepts is the compatibility policy: the broker may **add** fields (ignored), but a value outside what
the contract lists (a third screen orientation, say) is invalid, and the render fails, keeping the screen's frame.
`tests/test_config_store.py` pins each half.

## Running it in development

The render pool keeps nothing of its own: everything it draws is in Redis. To run or debug a worker by hand, point it
at a Redis that the broker and the providers fill (a local auth-broker with its Redis, and the providers), with the
environment the compose services have, in `dev/.env` (git-ignored):

```bash
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_PASSWORD=<the local Redis's password>
WORKER_NAME=render-worker-dev
LOG_LEVEL=DEBUG
```

Then run `uv run --env-file dev/.env countdown-render-worker` (and `countdown-render-ticker` beside it, or queue a
screen yourself: `LPUSH v1:render:jobs <device id>`), or the VS Code "Python Debugger: countdown-render-worker"
configuration, which runs `countdown_server.render_pool` with `dev/.env`. Only a screen the broker lists in
`v1:render:screens` is drawn. Never point a development worker at production's Redis while its workers run: the
queue's leases keep two from drawing one screen at once, but a development build would write its frames to the screens.
Without a Fluent Bit on `127.0.0.1:4318` the worker logs a metrics warning each minute, which is expected.

## Visual Regression Testing

The rendered e-paper screen is tested by comparing it against golden PNGs
checked into `tests/images/`, rather than only asserting on the data that
feeds it. `tests/test_display_snapshots.py` builds each scenario (arrivals,
energy, weather, Spotify now-playing, empty states, etc.) directly from
representative data and renders it through the real layouts (`rendering.render`).

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

Each tagged release builds the wheels and the `countdown-server` and `countdown-providers` Docker images, and deploys
the render pool and the providers (see `.github/workflows/release.yml` at the repo root and
[`server/deploy/`](server/deploy/)).

There's no local config file. A screen registers itself with
[auth-broker](https://github.com/nvorkinn/auth-broker); its owner pairs it at
https://nikolaivorkinn.com and picks a name (the slug) for it; the broker then lists it in `v1:render:screens`, and the
pool draws it. From then on everything is managed on the broker's pages: TfL stops,
weather location, Glowmarkt energy account, and connecting Spotify (the broker
handles the OAuth handshake and its music provider fetches now-playing into the store, so the render never holds a
Spotify token itself). The pool's own settings are `REDIS_URL` and `REDIS_PASSWORD` for the store, and `WORKER_NAME`;
everything else comes from the broker, through the store.

Once paired, an API that still needs setting up shows "Not configured" in its area (see
[How it works](#how-it-works)).

## Logs

The render pool and the providers log one JSON object per line on stderr (`countdown_core/core/log_format.py`):
`level` (`debug`, `info`, `warn`, `error`, `fatal`), `_msg`, `logger`, `source` (file:line), and `exception` when there
is a traceback, kept inside its record. The render pool's workers and ticker add `worker`, and name the screen in the
message; the providers add `device` (`DEVICE_NAME`, else the hostname). There is no timestamp: the container's log
driver stamps each line. `LOG_LEVEL` sets the level (default INFO); `asyncio`, `PIL` and `opentelemetry` stay at
WARNING, and `urllib3` is off, as its retry warnings and request lines quote the URLs it asks.

Nothing the render pool or a provider logs may say where a screen is: no postcode, place, district or coordinates, nor
a URL with one in it, such as a failed request's text (`log_format.error_reason` logs its type and status instead).

The host's Fluent Bit ships the containers' json-file logs to VictoriaLogs, naming each stream after the container's
`tag` log option, which the compose file sets to the service's name (`render-worker-1`, `provider-tfl`, ...). The
dashboards are in auth-broker (`docs/architecture.md`).

## Metrics

The render pool's workers report their metrics by screen, with a `device` attribute (the slug), as OTLP/HTTP to the
host's Fluent Bit at `http://127.0.0.1:4318/v1/metrics`: `countdown.render.duration`, `countdown.render.count` (by
`outcome`: `written`, `unchanged` or `failed`), `countdown.render.last_success`, `countdown.render.expired_panels` (by
`panel`), `countdown.render.lag`, `countdown.render.queued`, and the gauges `countdown.api.status` (by `api` and
`status`), `countdown.problem` and `countdown.stage` (by `stage`: `pairing` or `running`). The resource's `host.name`
is the worker (`render-worker-1`, `-2`). The counter and the histograms also carry the worker as `worker`, since the
host's Fluent Bit doesn't pass a resource's attributes (`host.name`) on to VictoriaMetrics: without it the two
workers' counters would share one series there. See `server/src/countdown_server/pool_metrics.py` for how to read them
across the two workers, and auth-broker's README ("Dashboards") for the dashboards. The per-screen renderer's
`countdown.display.connected` and `countdown.broker.last_sync` went with it.
