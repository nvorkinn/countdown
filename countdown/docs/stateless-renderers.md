# Stateless renderers

**Status:** proposal, for review. Nothing here is built yet, apart from the render state's shape and its handoff
rule (`countdown_core/core/render_state.py`), which nothing uses yet.

The goal is that a renderer stops being a long-lived container per screen that sleeps and polls. Instead it becomes
a job that runs, draws one frame and exits, started when the screen needs a new frame. Every panel already reads
only the store. Two pieces of work in flight remove the last two things a renderer fetches for itself: the postcode
lookup (`v1:location:<place>`) and the screen's config (`v1:config:<device>`, with a nudge). Once they land, a render
is a pure function of what's in Redis and the time, plus a little state carried from the frame before.

## The recommendation, in short

- **Who renders:** a small pool of warm worker processes (a compose service, `render-worker`, sharded by device id
  the way the providers are), not a container per render. A cold container costs about 12 to 24 times the CPU of a
  render in a warm process (see [Capacity](#capacity)).
- **When:** a per-screen clock, which is what the loop does now: every 60 s, or the providers' pace while one asks
  for faster. A config save renders at once. Later, renders can be timed to land just before the screen's next poll,
  because the broker knows when that will be. Data-change events (keyspace notifications, or a channel the
  providers publish) aren't needed: the screen pulls at most every 20 s, so a frame made sooner than its next poll
  is never seen sooner.
- **State:** almost none is left once page rotation is worked out from the clock and "keep the last good data" moves
  into the store's own keys (a longer TTL, with `fetched_at` saying how fresh the value is). What remains is the
  handoff (the last frame's hash and the pace it carried), about 100 bytes, in `v1:render-state:<device id>` with a
  TTL.
- **Handoff:** hash the 48,000-byte frame as the broker does. Skip the upload when the hash and pace say the broker
  already has it, by the rule the renderer follows now (`RenderState.needs_put`). Later, consider handing frames
  over through Redis instead of `PUT /api/frame`. Then a renderer needs no broker identity at all, and the
  orchestrator, the claim tokens and the per-screen volumes can go.
- **Migration:** eight steps, each shippable and reversible on its own. The per-screen containers keep running until
  the pool has rendered every screen for a while. See [Migration](#migration-strangler-fig).

## Where we are

### The orchestrator (`countdown/orchestrator/`)

The renderer itself is `countdown/server/` (`countdown_server`, its `ServerTarget` and its image), drawing with
`countdown/core/`. Its deploy, `server/deploy/docker-compose.yml`, runs the orchestrator and the providers, not the
renderers.

`main.run()` polls the broker every `POLL_INTERVAL_S` (10 s) for assignments and calls `reconcile()`, which makes
the renderers match them through `DockerRuntime` (`runtime.py`).

- **One container per assignment.** `DockerRuntime` has no pool, warm or otherwise: its README says "there's no pool
  to wait in". `_run()` makes `renderer-<slug>` from `RENDERER_IMAGE` with host networking, `restart: unless-stopped`,
  256 MB and half a CPU, and two volumes (`renderer-<slug>-data` for the identity, `-key` for its Fernet key).
- **Environment:** `BROKER_URL`, `DEVICE_NAME=<slug>`, `CLAIM_TOKEN` (the real one until claimed, then
  `already-claimed`), and `REDIS_URL` and `REDIS_PASSWORD` when `RENDERER_REDIS_URL` is set.
- **Labels:** `countdown.managed=1`, `countdown.slug`, `countdown.image` and `countdown.store`. A container whose
  image or store label isn't the current one is removed and made again, keeping its volumes (`_is_current`), so a
  release upgrades every renderer.
- **Statuses:** `suspended` stops a renderer (container and volumes kept) and `deleting` removes it with its volumes.
  A renderer no assignment wants is logged and left alone. `MAX_RENDERERS` (20 in the compose file) caps how many
  run at once.
- **Claiming:** the renderer claims its screen once, with `POST /api/renderers/claim {secret, claim_token}`
  (`countdown_credentials.registration.RendererRegistrar`). From then on its own secret, kept encrypted in its
  volume, is its identity for `GET /api/config` and `PUT /api/frame`.

### The renderer's loop (`countdown_core/core/`)

`app.run()` registers, waits for a config (`wait_for_config`), then runs `DisplayLoop.run()` forever. Each cycle:

1. `ApiRegistry.update_all()` polls the clients that are due (`AbstractClient.is_due`: a full `poll_interval`
   since `last_updated`; one minute by default, every cycle for the developer client). They run concurrently, in
   threads. A client that raises, or isn't due, keeps its last panel (`ApiRegistry.panels`).
2. `ApiRegistry.pace()` reads the fastest `v1:pace:<source>:<id>` among the keys its clients watch
   (`AbstractClient.pace_keys`, `reader.fastest_pace`), and `DisplayController.set_pace` passes it to the target.
3. `DisplayController.display_screen()` draws the frame (`rendering.render`, with the layout picked again every
   frame) and paints it only if the picture differs from the last one (`_show`). Otherwise it calls the target's
   `renew()`.
4. It sleeps `DisplayLoop.sleep_s()`: the config's `interval` (60), or the pace if that's shorter, never under
   `MIN_PACE_S` (20).
5. It re-reads the config (`refresh_broker_config`), rebuilding only the clients whose `needs_refresh()` says so. A
   new language or time zone rebuilds them all, as `set_locale()` is process-global.

The pace: a provider whose data turns bursty (the developer provider, while a release runs) defines `pace()`, and
`ProviderRunner._write_pace` writes it to `v1:pace:<source>:<id>` with `PACE_TTL_S` (60 s), or deletes it.

### How a frame reaches the screen

- **Upload:** `ServerTarget.paint()` (`countdown_server/server_target.py`) PUTs the whole 800x480 1-bit frame (48,000
  bytes) to `PUT /api/frame`, with `X-Refresh-Hint: <pace>` while there is one. `renew()` PUTs the last frame again
  while a pace is asked for, or once more after it stops.
- **The broker** (auth-broker `routes/frames.py`) keeps one frame per device in SQLite, with an ETag
  (`blake2b(frame, digest_size=8)`), `rendered_at`, and the pace leased for `PACE_LEASE_S` (180 s)
  (`services/pace.py`).
- **The screen** (the ESP32) polls `POST /api/frame` with `If-None-Match`, gets 200 or 304, and sleeps `Retry-After`:
  `BASE_INTERVAL` (60), or the leased pace (20 to 59), or `NO_FRAME_RETRY_AFTER` (20) before the first frame. It
  repaints only on a 200.

### The providers' nudge

`ProviderRunner` (`countdown_providers/runner.py`) runs a cycle at once, then on every epoch-aligned tick of its
`interval_s`, or early when the broker publishes on `v1:sub:changed` after rewriting the subscriptions (auth-broker
`routes/pages.py`, `_write_subscriptions_to_redis`). `serve_all()` runs several providers in one process, each in its
own loop (the TfL notices). Each cycle writes every subscribed key with `SET ... EX ttl_s`, **whether or not the value
changed**: departures every 30 s, weather every 10 min, and so on.

### Redis

On the broker's host, `127.0.0.1:6379`: 128 MB, `maxmemory-policy volatile-lru` (only keys with a TTL are evicted),
**no persistence** (`save ""`, `appendonly no`) and no keyspace notifications (`notify-keyspace-events` unset)
(auth-broker `docker-compose.yml`).

### What a renderer keeps in memory between frames

Everything a run-and-exit job would lose, and what it becomes:

| State | Where | What it does | Stateless |
|---|---|---|---|
| Last panel per API | `ApiRegistry.panels` | a client that raises (a `LookupError` for a missing key, `RedisError`) keeps its last panel, for as long as the client lives | the last good *data* stays in the store key (longer TTL, `fetched_at` for freshness); see [Last good](#last-good-data) |
| Departures page | `DeparturesClient.current_page`, `stops_per_update` | a page of stops per poll, `stops_per_update` from the layout (`ApiRegistry.show_stops`) | page from the clock; per-page count from the layout each render |
| Weather tab | `WeatherClient._next_page`, `_pages`, `_panel`, `_last_attempt`, `_utc_offset` | a tab per poll; the store read again only every 10 min or when the date changes | tab from the clock; read every render (a GET) |
| Energy page | `GlowClient.page_index` | 24 h, 31 days, year in turn | page from the clock |
| Spotify page | `SpotifyClient.page` | player, then top lists, a page per poll; an empty page gives way to the next | page from the clock, the next one if empty |
| Developer summary | `DeveloperClient.summary` | `DeveloperNoticeSource` reads the client's last answer | the source reads the store itself. Today it's racy: the clients run concurrently, so it reads the cycle before's |
| Notice board | `NoticeBoardClient.fetched`, `last_tried` | each source's last notices, kept up to `STALE_AFTER` (1 h) when it fails; each source polled on its own `refresh_interval` | every source read every render (they're all store reads or local sums); last good in the store keys |
| Notice board place | `location`, `location_tried`, `unknown_postcode`, `sources` | the postcode looked up once, retried every 10 min | read from `v1:location:<place>` each render (in flight) |
| Client lifecycle | `AbstractClient.status`, `last_updated`, `cache` | due-ness, the status the metrics report, the pairing code's change check (`BrokerClient`) | clients built fresh each render (all cheap: a few ms); status per render |
| The picture on the screen | `DisplayController._shown`, `_last_full_refresh`, `_pending` | skip a repaint of the same picture; partial versus full refresh; retry a pairing screen | the frame's hash in the render state. Partial refresh doesn't apply: `ServerTarget` always sends the whole frame and the screen decides |
| What the broker has | `ServerTarget._last_frame`, `_sent_pace`, `_pace` | renew the pace with the same frame | `RenderState.handoff` (this PR) |
| Loop | `DisplayLoop.interval`, `_pace`, `_published_stage`, `pairing_code_panel` | when to wake, reporting a new stage at once | per render |
| Locale | `i18n._language`, `_timezone` (module globals) | `_()` and `local_zone()` | set per render, one render at a time per process (or a `ContextVar` later) |
| Health | `OtlpPublisher`, `DeviceStatus` | gauges every minute from a background thread | per-render metrics from the worker; see [Observability](#observability) |
| Identity | `/data/.auth_broker_device` (volume, not memory) | authenticates config and frames | see [Identity](#identity) |
| Caches | fonts (`utils.fitted_font`, `_font_at`), catalogues (`i18n._catalogue`), Redis clients (`reader.store`) | | shared by every render in a warm worker; what a cold job pays again each time |

Clock-dependent text means a frame can change while no data does. Departures count down from `expected_at` when the
panel is made (`StoredArrival.of`). "Today" and "Tomorrow" in notices, the weather's "Tonight" and the energy
windows depend on the hour. And `rendering.share_spotify_area` gives the developer panel Spotify's area one minute in
three (`DEVELOPER_TURN`). Whatever triggers renders, something has to tick at least once a minute.

## Triggers

| Trigger | How | For | Against |
|---|---|---|---|
| **Keyspace notifications** | `notify-keyspace-events K$` on Redis; subscribe to `__keyspace@0__:v1:*` | no provider changes | off now (an auth-broker Redis config change); fire on every `SET`, and the providers rewrite every key every cycle unchanged (each stop every 30 s); need a reverse index from key to screens, which nothing keeps; fire-and-forget (lost while no one is subscribed); CPU on every write |
| **Provider-published changes** | the runner compares each value with the one it replaces (`SET ... GET`, leaving `fetched_at` out, as it changes every time) and publishes the ids that really changed on `v1:changed:<source>`, once per cycle | only real changes; one message per cycle | changes the runner here and the broker's three provider loops; still needs key-to-screen mapping; lossy like any pub/sub |
| **Polling (a clock)** | each screen due every `interval`, or its pace | what we do now; covers clock-dependent text; no new moving parts | renders when nothing changed (made cheap by the hash check) |
| **Config nudge** | the broker publishes after writing `v1:config:<device>` (in flight); the worker makes that screen due now | a saved setting shows at the next poll instead of up to a minute and a poll later | none |
| **Screen fetch** | when the broker answers a poll with `Retry-After: R`, it records when the next poll is due (`v1:poll:<device id>`, epoch seconds, TTL a few R); the worker renders that screen a few seconds before | renders exactly as often as frames are fetched, freshest at fetch time; a screen that's off costs nothing | a write to Redis on each poll (one `SET` per screen per minute); the broker learns about rendering |
| **Render on fetch, synchronously** | the broker renders, or waits for a render, inside the screen's request | always fresh | the ESP32 waits for the render (100 ms to seconds), a gunicorn worker is held, a slow render stalls the screen |

**Recommendation.**

1. Start with the clock (exactly today's cadence) plus the config nudge.
2. Add screen-fetch timing when the pool is in, to drop wasted renders and make each frame as fresh as possible when
   it's fetched.
3. Don't build data-change events. The screen polls at least 20 s apart (`MIN_INTERVAL`), so a frame made the moment
   data changes is seen no sooner than one made just before the next poll. On average either is half a poll after
   the change, where the loop now averages a whole one. The one place an event would help is the start of a burst:
   the first fast refresh waits for the next 60-s poll, as it does now. If that matters, the developer provider can
   nudge the screen when it first writes a pace key; it already knows the device.

## Who runs the job

### A. The orchestrator starts a short-lived container per render

`docker run --rm` with the screen's volumes, per trigger.

- **For:** the strongest isolation. Each job sees only its own identity, under its own memory and CPU limits, and a
  crash takes one frame with it. It's literally run-and-exit.
- **Against:** the cost and the moving parts.
  - Each render pays Docker's create and start (not measured here: no daemon in the sandbox; typically 0.5 to 1.5 s
    and a few hundred ms of dockerd, containerd and runc CPU), then Python's imports (0.6 s) and a cold first render
    (0.15 s CPU). That's roughly 1 CPU-second per render against 0.05 s warm.
  - It puts `docker.sock`, root on the host, on the hot path of every frame.
  - The orchestrator turns from a 10-s reconciler into a scheduler.
  - The log driver makes a new json-file per container run.
  - Each frame has 1 to 2 s of latency.

### B. A pool of warm workers (recommended)

A compose service, `render-worker`, from the renderer's image with another command. It runs as a few processes, each
rendering the screens in its shard (`sha256(device id) % SHARD_COUNT`, `providers/sharding.py`, as the providers do).
For each screen that's due it:

1. reads the config (`v1:config:<device id>`), the render state and the panels' keys;
2. builds the clients fresh, draws, encodes and hashes the frame;
3. hands it off if `needs_put` says so;
4. saves the state and works out when the screen is next due.

- **For:**
  - Imports, fonts and catalogues are paid once per process, so a render is about 50 ms of CPU.
  - About 120 MB per worker, however many screens, where now it's about 115 MB per screen (20 screens are 2.3 GB).
  - Nothing runs for a screen between frames, and adding workers is changing `SHARD_COUNT`.
  - No `docker.sock`.
  - One place for per-render logs and metrics.
- **Against:**
  - One worker draws many screens. The locale is a module global, so a process renders one screen at a time (or
    `i18n` moves to a `ContextVar`).
  - A render that hangs or crashes the process holds up its shard until it's killed and restarted (see
    [Failure](#failure-and-backoff)).
  - The workers need a way to hand off any screen's frame (see [Identity](#identity)).

**B2, a shared due-queue,** is the variant for later, if shards turn out uneven or the broker should schedule renders
directly: one sorted set, `v1:render:due` (member: device id, score: when due), claimed by any worker with a short Lua
script (take the earliest due without a lease, take its lease, push its score out by the lease so a crashed worker's
screen comes due again), and rescheduled with `ZADD XX` only if no nudge moved it meanwhile. It coalesces triggers per
screen and gives work-stealing, at the cost of the Lua and of rebuilding the set after a Redis restart (no
persistence). Start with shards (B), which need none of that.

### C. In-process workers in a service that already runs

- **In the orchestrator:** no. It holds `docker.sock`, and rendering decodes untrusted input (upstream text, cover
  JPEGs through Pillow), so a decoder bug there would be root on the host.
- **In the broker:** no. It's the hub for every identity and secret, and a render is CPU-bound work in its gunicorn
  workers.
- **Keep a container per screen, but make it wait for a nudge instead of sleeping:** this is a good stepping stone
  (state moves out, triggers arrive), but not an end state. It keeps one process, 115 MB and an identity per screen.

## Per-screen state

### Page rotation: from the clock

Every rotating client turns a page per poll, and every one polls once a minute. So the page is a function of the
time: `page = epoch_minute % pages`. Spotify's skip-an-empty-page becomes "this minute's page, else the next one".
`share_spotify_area` already works this way (`now.minute % DEVELOPER_TURN`). Spotify has 4 or 8 pages and the
developer panel's turn comes every 3rd minute, so every Spotify page still gets shown.

- **What it gives:** two renders in the same minute draw the same page. So a nudge or a retry doesn't skip a page,
  the hash check sees "nothing new", and the state needs no counters.
- **What changes:** every screen turns its pages on the minute, together, and a config change no longer starts the
  departures on page 0. A per-device phase (`hash(device) % pages`) would desynchronise the screens if that's wanted.

### Last good data

Today a client that can't read its key keeps its last panel for as long as the client lives. That's frozen, too: a
kept departures panel still says "in 5 min" an hour later, because the minutes are counted when the panel is made. The
notice board keeps each source's notices for at most an hour. A job that exits keeps nothing, so where does "last
good" go?

| | How | For | Against |
|---|---|---|---|
| **L1: per-screen copies** | the render state holds the raw value of each key it read, with when; a read that misses falls back to it, within a maximum age | no provider changes | a copy per screen of everything it reads: 30 to 150 KB with weather and notices (MetAlerts is all of Norway), rewritten as often as the data changes |
| **L2: shared copies** | `v1:last:<key>`, written by whichever renderer reads a changed value | one copy per key | the renderers write the store; double the memory for every key read |
| **L3: the store's own key (recommended)** | the providers keep each key for a "give up" horizon (an hour, as the notice board's `STALE_AFTER`) instead of about twice their interval, and `reader.read()` checks `fetched_at` against how fresh the data must be | no copies and no extra writes; every stored value already has `fetched_at` (`store.models.Stored`); a kept departures panel counts down properly instead of freezing | changes the providers' TTLs in both repos, and "missing key means the provider is down" becomes "old `fetched_at` means the provider is down"; keys outlive their subscription by up to an hour (a few KB each) |

With L3, a read too old to trust is drawn as the kept panel is now, but the client reports `error` so the metrics
still show the provider's down. The pace keys keep their 60 s (a pace must lapse), and the covers keep theirs
(renewed while shown).

### The render state key

`v1:render-state:<device id>`, a string of `RenderState` JSON, with a TTL of a day. A screen gone for a day is
suspended by the broker anyway. A lost state (expired, evicted under `volatile-lru`, or a Redis restart) only means
the next frame is handed off whatever it is.

```json
{"version": 1, "handoff": {"etag": "cf4d49a0c56195f9", "pace": 20, "at": "2026-10-18T12:00:00Z"}}
```

- **Writes:** one `GET` before each render and one `SET ... EX 86400` after any render that hands a frame off.
- **Fields:** a version bump is for changes an older reader can't read, and a state that can't be read is treated
  as none (`render_state.decode`). Later fields, such as the failure count for backoff, are added with defaults.
- **Keys:** the builder goes in `keys.py` and its row in the providers' README key table, with the step that first
  writes it.

### Identity

After config-in-store, the only call a renderer makes to the broker is `PUT /api/frame`. That's the only thing a
pool's identity question is about:

1. **Hand frames over through Redis.** The worker writes `v1:frame:<device id>` (the 48,000 bytes, with its ETag,
   pace and `rendered_at`), and the broker's `GET` and `POST /api/frame` read it there, falling back to the SQLite
   row. Then renderers need no broker secret, no claim, no volumes and no orchestrator: the broker writes the list
   of screens to render itself.
   - **Trust:** this doesn't widen trust. Whoever can write Redis can already put any text on any screen, through
     the providers' keys.
   - **Memory:** 48 KB per screen, kept without a TTL so `volatile-lru` never evicts it, and deleted when the broker
     forgets the device.
   - **Redis restart:** the frames go too. The broker falls back to SQLite, or answers 404 with `Retry-After: 20`,
     and the e-paper keeps its picture until the next render.
2. **Keep `PUT /api/frame`, with each screen's identity copied into a volume the pool shares.** The orchestrator
   copies `renderer-<slug>-data/.auth_broker_device` and `renderer-<slug>-key/countdown/credentials-key`. `Registrar`
   already takes `credentials_file` and `cipher`, so the format is unchanged. The pool then holds every renderer
   secret, as the host's volumes do now. New screens are claimed by the worker with the claim token, from a list
   the orchestrator writes.
3. **A broker credential for the pool** (`RENDER_TOKEN`, like `ORCHESTRATOR_TOKEN`) allowed to `PUT
   /api/devices/{id}/frame` for any device. That's an additive route and one secret, but a new power: today the
   orchestrator token can't paint a claimed screen.

Option 1 is the simplest end state. Option 2 changes nothing in the broker. See the open questions.

## Handoff and de-duplication

- **The hash:** `render_state.frame_etag()`, the broker's own ETag.
- **When to upload:** `RenderState.needs_put(etag, pace)`. A frame whose hash differs from the last one handed off
  always goes. The same one again goes only while a pace is asked for, which renews the broker's 180-s lease, or once
  after it stops, which ends the pace at once. Otherwise the upload is skipped.
- **After an upload:** `handed_off()` records the hash, the pace and when, but only after the broker answered 2xx. A
  refused frame goes again next time.
- **Proof:** `tests/test_render_state.py` runs the same sequence of pictures, paces and broker failures through
  today's `DisplayController` and `ServerTarget` and through the rule with an in-memory store. The uploads (bodies
  and hints) are the same.
- **Later:** while a pace holds, the same frame needn't go every 20 s. Once its lease is half used (90 s) is enough.
  That cuts the re-uploads of an unchanged frame during a release by about 4 times. It's a small change to the rule, and the state already
  keeps `at` for it.
- **The broker's "Drawn" time:** it shows `rendered_at`, so a skipped upload stops it moving. That's already so for
  an unchanged picture now. A worker's own metrics are the liveness signal (see [Observability](#observability)).

## Concurrency

There's one render per screen at a time:

- **Shards:** each screen belongs to one worker (B). Within a worker, renders are serial.
- **The lease:** a worker takes `v1:render:lease:<device id>` (`SET NX PX 30000`, its own token) before rendering
  and deletes it after, if it's still its own. That covers the moments two workers think they own a screen: a deploy
  that changes `SHARD_COUNT`, or a worker replaced while its old copy finishes.
- **Moving a screen between a container and the pool:** the orchestrator does it in one direction at a time, and the
  container's renders take the same lease once the state is in Redis (step 4). So even a mistaken overlap can't draw
  one screen twice at once.
- **A stalled worker:** the lease doesn't fence the PUT. A worker that stalls beyond its lease and then uploads
  could overwrite a newer frame with an older one, for one poll. Checking the lease is still its own just before the
  upload narrows that window, and the next render puts it right.

## The pace and bursts

- **Unchanged:** providers write `v1:pace:<source>:<id>` (60 s TTL). Each render reads the fastest the screen's
  clients watch (`ApiRegistry.pace()`, `reader.fastest_pace`).
- **The next render:** the screen is due again in `sleep_s()`: `min(interval, max(pace, MIN_PACE_S))`. Moved to a
  pure function, it's what the worker's schedule uses.
- **The hint:** the upload carries `X-Refresh-Hint`, and `needs_put` renews it while the pace holds.
- **With screen-fetch timing:** the broker's `Retry-After` already follows the pace, so the renders follow it too.
- **Possible later:** the broker could read `v1:pace:developer:<device id>` itself, as it knows the screen's
  developer panel is on. Then renderers wouldn't carry the pace at all. That's not needed for this work.

## Failure and backoff

| What fails | What happens |
|---|---|
| A panel's read (missing or stale key, `RedisError` on one read) | the panel's problem, not the render's: the stale value or a `MessagePanel`, as now |
| The render raises | logged with its traceback, as the loop does now, counted, and the screen is due again after 30 s, doubling to 5 min (`app.py`'s `INITIAL_RETRY_S` and `MAX_RETRY_S`); the count is kept in the render state and cleared by a success; the broker keeps serving the last frame |
| The upload fails | the state isn't moved, so the next render sends it again; scheduled as usual (a broker outage isn't the screen's fault); logged by error type and status, never the URL |
| A render hangs | the worker's watchdog (a deadline per render, e.g. `faulthandler.dump_traceback_later(30, exit=True)`) ends the process; compose restarts it; the lease lapses |
| A screen crashes its worker every time (a decoder bug on one cover, say) | the attempt is counted in the state *before* the render and cleared after, so a crash still counts. After 3 in a row the screen is quarantined (due every 10 min, an `error` log and metric) and the rest of its shard carries on |
| Redis is down | workers can't read config, data or state: they retry every 5 s (as the providers' `RESUBSCRIBE_S`), and every screen keeps the last frame the broker has. After a restart the state is gone (one extra upload each) |
| The broker is down | the screen can't fetch anyway; renders carry on and their uploads fail and are retried |

## Observability

**Logs.** One JSON line per render at `debug`. At `info` only when a frame was handed off (about one a minute per
screen) or a render failed. The fields:

- `device`: the slug, as the renderer logs now;
- `trigger`: clock, config, poll or retry;
- `duration_ms`, split into reading, drawing, encoding and the upload;
- `changed`, `put_status` and `pace`;
- `stale`: the key families read stale, e.g. `v1:notices:floods`.

Never key ids, which hold places (`v1:notices:floods:SE173LL`), and never URLs.

**Metrics** (OTLP to the host's Fluent Bit, as now):

- `countdown.render.duration` (histogram), `countdown.render.count` (by `outcome`: handed off, unchanged, failed)
  and `countdown.render.put` (by status);
- `countdown.render.lag`: when the render started, less when it was due. This is the pool's capacity signal: if it
  grows, add a worker;
- `countdown.render.due`: screens due and not yet rendered;
- the existing per-screen gauges (`countdown.api.status`, `countdown.problem`, `countdown.stage`,
  `countdown.broker.last_sync`), published by the worker. They carry a `device` attribute, because the resource's
  `host.name` is now the worker. That moves the auth-broker dashboards that group by `host.name`.

## Capacity

Measured in this sandbox, on 2 vCPUs of an Intel Xeon at 2.8 GHz, shared with other jobs, so the spread is wide. The
store was fakeredis in-process, and the screen 800x480 landscape with a Tube station and a bus stop, the weather,
energy and the notice board. Material Symbols couldn't be downloaded here, so a bundled font stood in for it. The
production host's CPU isn't known here; these are orders of magnitude, not a benchmark.

| | Wall | CPU |
|---|---|---|
| `python -c pass` | 20 ms | 20 ms |
| `import countdown_server.main` (pydantic, requests, redis, Pillow and fonts, babel, OpenTelemetry, countdown_api) | 0.60 to 0.68 s | 0.55 to 0.6 s |
| First render in a fresh process (build clients, read, draw, encode, hash) | 120 to 220 ms | 145 ms |
| A fresh process to its first frame | 0.7 to 1.2 s | about 0.7 s |
| A warm render, clients built fresh each time, median (p95) | 51 to 84 ms (68 to 146) | 49 ms (63 to 73) |
| of which drawing / reading / encoding and hashing (median) | 35 to 62 / 4 to 6 / 6 to 10 ms | |
| Resident memory of one process after rendering | | 115 MB |
| `docker run` of a cached image | not measured (no Docker daemon here); typically 0.5 to 1.5 s | a few hundred ms in the daemon |

To measure the container's cost on the host:
`time docker run --rm --network host --entrypoint python ghcr.io/nvorkinn/countdown-server:<release> -c "import countdown_server.main"`.

A render per screen per minute (a screen in a release renders three times as often while it lasts):

| Screens | Renders a minute | A: a container per render | B: warm workers | Today: a container per screen |
|---|---|---|---|---|
| 10 | 10 | about 10 CPU-s a minute (17% of a core) | 0.5 to 0.75 CPU-s (about 1%), 1 to 2 workers | 1.2 GB resident |
| 100 | 100 | about 100 CPU-s (1.7 cores) | 5 to 7.5 CPU-s (8 to 12% of a core), 2 workers | 11.5 GB (past `MAX_RENDERERS`) |
| 1,000 | 1,000 | about 1,000 CPU-s (17 cores) | 50 to 75 CPU-s (0.8 to 1.25 cores), 3 to 4 workers | not feasible |

One worker rendering one screen at a time manages about 800 renders a minute at full load (75 ms each), so it's
comfortable up to about 400 screens at a minute each. Two workers give headroom and survive one restarting. Redis
costs per render: about 10 to 15 GETs and MGETs, plus one GET and at most one SET for the state, on loopback.

## Migration (strangler fig)

Each step ships on its own, in a normal release, and can be rolled back without the steps after it.

**1. Rotation from the clock.**

- **Change:**
  - Departures, weather, energy and Spotify pick their page from the minute, not from a counter.
  - `DeveloperNoticeSource` reads the store, not `DeveloperClient.summary`.
  - The weather reads the store every poll.
- **Rollback:** revert the release.

**2. Last good in the store (L3).**

- **2a:** the providers' TTLs become the give-up horizon of an hour: pi-dashboard's in one release, auth-broker's
  (glow, spotify, developer) in its own. A down provider now gives older values, which re-render, and departures
  count down rather than freeze.
- **2b:** `reader.read()` checks `fetched_at` against each kind's freshness, and the clients report `error` on stale
  data.
- **Rollback:** the old TTLs.

**3. Config and locations in the store (in flight).** `v1:config:<device>` with its nudge, and `v1:location:<place>`.
The loop wakes on the nudge.

**4. A render is a function.**

- **Change:**
  - Extract `render_once()` from `DisplayLoop`, `ApiRegistry` and `DisplayController`: build the clients from the
    config, read, draw, hash, `needs_put`, upload, save the state.
  - `DisplayLoop` calls it each cycle, with a `RedisRenderStateStore` (`v1:render-state:<device id>`) behind a
    setting (`RENDER_STATE=redis`, `memory` by default), and takes the lease.
  - Still a container per screen. A restart no longer re-uploads or forgets the pace.
- **Rollback:** the setting.

**5. Frames through the store (if chosen; see [Identity](#identity)).**

- **Change:** the renderer writes `v1:frame:<device id>` as well as PUTting, and the broker serves Redis first, then
  SQLite. When that has run for a while, the PUT goes.
- **Rollback:** a broker setting to serve SQLite only, while the renderer still PUTs.

**6. The pool, one screen first.**

- **Change:**
  - Add the `render-worker` service to `server/deploy/docker-compose.yml` (two shards).
  - The orchestrator gets `POOL_SLUGS`. For each of those screens it stops the container (keeping it and its
    volumes) and lists the screen in `v1:render:devices` (device id to slug; the assignments list gains
    `device_id`, an additive change in auth-broker). It lists them again every pass, so a Redis restart heals in 10 s.
  - Without step 5, it first copies the identity into the pool's volume.
  - Canary on one screen for a few days, then all.
- **Rollback:** take the slug out of `POOL_SLUGS`. The orchestrator unlists the screen, waits for its lease to lapse,
  and starts its container again.

**7. Renders timed to the screen's polls.**

- **Change:** the broker records each screen's next poll (`v1:poll:<device id>`), and the workers render a few
  seconds before it, with the clock as a fallback at most 5 min apart.
- **Rollback:** the workers ignore it.

**8. Retire the per-screen containers.**

- **Change:** the orchestrator removes them, and their volumes a few weeks later. With step 5 the claim flow and the
  orchestrator can then go too, with the broker writing `v1:render:devices` itself.
- **Rollback:** until the volumes are deleted, put the slugs back on containers.

## What lands with this proposal

`countdown_core/core/render_state.py`, used by nothing yet:

- `RenderState`: the handoff, versioned and frozen;
- `RenderState.needs_put` and `handed_off`: today's upload rule, over hashes;
- `frame_etag`: the broker's ETag;
- `encode` and `decode`: an unreadable state counts as none;
- `RenderStateStore`: the interface, with `InMemoryRenderStateStore`, which keeps the JSON a Redis store would.

`tests/test_render_state.py` pins the hash, the rule and the round trip, and checks the rule against today's
`DisplayController` and `ServerTarget`. The Redis-backed store (step 4) is a `GET` and a `SET ... EX`.

## Open questions

1. **Identity:** hand frames over through Redis (no renderer secrets, and the orchestrator can go), keep `PUT
   /api/frame` with identities copied into the pool, or a broker token for the pool?
2. **Last good:** may the providers' TTLs become an hour, with freshness judged by `fetched_at` (L3)? Or would you
   rather keep TTLs as they are and have per-screen copies in the render state (L1)? And how stale may a departures
   value be before it's hidden rather than drawn?
3. **Rotation:** is it fine for every screen to turn its pages together on the minute? And for a config change not
   to start the departures from page 0? Or do you want a per-screen phase?
4. **Metrics:** may the per-screen gauges move from the resource's `host.name` to a `device` attribute, with the
   auth-broker dashboards updated to match?
5. **Logging:** an `info` line per handed-off frame (about one a minute per screen into VictoriaLogs), or `debug`
   only, with metrics for the rest?
6. **Screen-fetch timing (step 7):** worth a Redis write on every poll in the broker? Or is the clock enough?
7. **The host:** what CPU does the renderers' host have, and how many screens should this plan for in a year? The
   capacity numbers come from an x86 sandbox.
8. **The config nudge:** which channel will `v1:config:<device>` publish on? The workers subscribe to it.
9. **Bursts:** should the developer provider nudge the screen when a release starts, so the fast refresh begins
   within seconds rather than at the next 60-s poll?
