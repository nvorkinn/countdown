# Stateless renderers

**Status:** decided. The design below follows Nikolai's answers to the proposal's open questions
([#205](https://github.com/nvorkinn/pi-dashboard/pull/205#issuecomment-6246057069), 2026-10-18), and is being built
in the steps of the [migration](#migration). Step 1, rotation from the clock, is the first.

Today every screen has its own long-lived renderer container that sleeps, wakes, draws and uploads. The goal is a
render that runs to completion and keeps nothing: read Redis, build the frame, write it, done. A small pool of warm
workers calls that render whenever a job for a screen arrives on a Redis queue.

## The design, in short

- **Who renders.** A pool of warm worker processes on the Oracle ARM host. Each is a thin wrapper that blocks on a
  Redis job queue and calls the render function for each job. The render function has no sleeps and no loops of its
  own.
- **What triggers a render.** A job on the queue, from one of three places:
  - a ticker, every minute on the minute, for every screen;
  - the broker, when a screen's config is saved;
  - a provider whose data has turned bursty (the developer provider when a release starts), for the screens
    subscribed to that data.
- **One render per screen at a time.** A job for a screen whose render is in flight is never dropped: it leaves exactly
  one follow-up render queued.
- **Frames go through Redis.** The render writes the screen's latest frame to `v1:frame:<device id>`, but only when its
  hash changed, and the broker serves it to the screen from there. Renderers hold no secrets, so the orchestrator,
  the claim flow and the per-screen volumes go.
- **Expiry.** Each provider's TTL is the only expiry there is. A panel whose key has expired says its data isn't
  available. There are no last-good copies, no per-screen state and no freshness check on top.
- **Rotation.** Each panel shows page `current minute mod its page count`, so every screen turns its pages together,
  on the minute.
- **Pacing.** One pure, well-tested function in the broker decides how long a screen sleeps after each poll, from the
  server's clock: just after the next render boundary, polling twice as fast as renders are made (renders every
  60 s, polls about every 30 s), faster while a provider's refresh hint is live. That logic lives nowhere else.
- **Metrics and logs.** Metrics carry a `device` attribute: render duration, success or failure, when the screen last
  got a good frame, panels drawn empty because their data expired, and frames skipped because nothing changed. Logs
  are for what goes wrong; there's no line per frame.
- **Not in this design:** partial e-paper refresh. That's a separate follow-up in the ESP32 firmware only (diff the new
  frame against the one shown, partially refresh one small 8-px-aligned box, a full refresh every N partials or few
  minutes), with no server or contract change.

## Where we are

### What runs now

- **The orchestrator** (`countdown/orchestrator/`). It polls the broker every 10 s for assignments, and keeps one
  `renderer-<slug>` container per screen through `DockerRuntime` (`runtime.py`). Each container has host networking,
  256 MB and half a CPU, and two volumes: the renderer's identity and its key. A renderer whose image or store label is
  out of date is made again, keeping its volumes. `suspended` stops a renderer and `deleting` removes it with its
  volumes.
- **The renderer** (`countdown/server/`, drawing with `countdown/core/`). It claims its screen once with the claim
  token (`POST /api/renderers/claim`), then `DisplayLoop.run()` cycles forever:
  - `ApiRegistry.update_all()` polls the clients that are due, and keeps each client's last panel when it raises.
  - `ApiRegistry.pace()` reads the fastest `v1:pace:<source>:<id>` the clients watch.
  - `DisplayController.display_screen()` draws, and `ServerTarget` PUTs the 48,000-byte frame to `PUT /api/frame`
    when the picture changed, with `X-Refresh-Hint`. `renew()` sends the same frame again while a pace is asked for,
    or once more after one ends.
  - It sleeps `DisplayLoop.sleep_s()`: the config's 60 s interval, or the pace, never under `MIN_PACE_S` (20).
- **The config is in the store** (auth-broker #186, pi-dashboard #208). The broker writes `v1:config:<device id>` (the
  `GET /api/config` body, less its secrets and with no pairing code) on every save and as it starts. Then it publishes
  the device id on `v1:config:changed` (`keys.CONFIG_CHANGED`). The renderer reads the copy (`store.config.read`),
  falls back to `GET /api/config` when there's none (an older broker, or a pairing code to show), and
  `ConfigWatcher` wakes the loop on the nudge.
- **Places are in the store.** `provider-locations` writes `v1:location:<place>` (`store.location.StoredLocation`).
  The notice board reads it, and so do the providers that need a postcode's place. So no renderer calls an upstream
  API any more. Its only calls to the broker are `PUT /api/frame`, and `GET /api/config` when the store has no copy.
- **Pacing is spread over five places**, which the broker's pacing function will replace:
  - providers write `v1:pace:<source>:<id>`, with `PACE_TTL_S` of 60 s;
  - the renderer reads the fastest of those (`ApiRegistry.pace`), sleeps for it (`DisplayLoop.sleep_s`) and sends it
    as `X-Refresh-Hint`, renewing it with repeat uploads (`ServerTarget.renew`);
  - the broker keeps the hint for `PACE_LEASE_S` (180 s) in the frame's row (`services/pace.py`);
  - the broker answers the screen's poll with `Retry-After`: `BASE_INTERVAL` (60), or the hint, or
    `NO_FRAME_RETRY_AFTER` (20) before the first frame.
- **The screen** (the ESP32) polls `POST /api/frame` with `If-None-Match`, repaints only on a 200, and sleeps for
  `Retry-After`. It has no clock of its own.
- **Redis** (on the broker's host, 127.0.0.1:6379) has 128 MB, `volatile-lru`, and no persistence. Keys without a TTL
  are never evicted.
- **Already in the code:** `countdown_core/core/render_state.py` (#205), used by nothing yet:
  - `frame_etag()`, the broker's ETag;
  - `RenderState.needs_put()`, today's upload rule;
  - a `RenderStateStore`.

  Step 4 keeps `frame_etag()` and removes the rest: the frame record in Redis holds the one piece of state that's left,
  the frame's hash.

### What a renderer keeps between frames, and what becomes of it

| State | Where now | Becomes |
|---|---|---|
| Last panel per API | `ApiRegistry.panels`, kept when a client raises | nothing: an expired key draws "Not available" (step 2) |
| Departures page | `DeparturesClient.current_page` | the clock (step 1) |
| Weather tab, and its 10-minute cache | `WeatherClient._next_page`, `_pages`, `_panel`, `_last_attempt` | the clock (step 1); the store read on every render (step 2) |
| Energy page | `GlowClient.page_index` | the clock (step 1) |
| Spotify page | `SpotifyClient.page` | the clock, the next page if this one is empty (step 1) |
| Developer summary | `DeveloperClient.summary`, read by `DeveloperNoticeSource` | the source reads the store itself (step 2) |
| Notice board's last notices | `NoticeBoardClient.fetched`, kept up to `STALE_AFTER` (1 h) | nothing: an expired source adds no notices (step 2) |
| Per-source refresh intervals | `NoticeBoardClient.last_tried` | every source read every render: they're all store reads or local sums (step 2) |
| Client lifecycle | `AbstractClient.status`, `last_updated`, `ApiRegistry`'s rebuilds | clients built fresh for each render; the status goes to the metrics (step 5) |
| The picture shown | `DisplayController._shown`, `_last_full_refresh`, `_pending` | the frame record's hash (step 4). Partial refresh moves to the firmware |
| What the broker has | `ServerTarget._last_frame`, `_sent_pace` | the frame record (step 4); the pace isn't the renderer's business any more (step 3) |
| Locale | `i18n._language`, `_timezone`, module globals | set at the start of each render; a worker runs one render at a time |
| Health | `OtlpPublisher` and `DeviceStatus`, per process | per-render metrics with a `device` attribute (step 6) |
| Identity | `/data/.auth_broker_device` in the renderer's volume | none: frames go through Redis (steps 4 and 9) |
| Caches | fonts, translations, Redis clients | kept per worker process, shared by every render; read-only |

## The decisions, in detail

### Frames through Redis

`v1:frame:<device id>` is a Redis hash with no TTL, so `volatile-lru` never evicts it. The broker deletes it when it
forgets the device. Fields:

- `frame`: the 48,000 bytes, as `PUT /api/frame` takes them now;
- `etag`: `frame_etag(frame)`, the same 8-byte BLAKE2b the broker uses for its ETag;
- `rendered_at`: when this picture was first written.

It's one latest value per screen, the same pattern as the providers' keys, so Redis doesn't become a messaging
platform.

**The render** compares the new frame's hash with `HGET v1:frame:<id> etag` and writes the record only if it differs.
An unchanged frame costs one small read and no write. When the screen last had a good render is a metric, not a field
(see [Observability](#observability)), so a skipped write stays skipped.

**The broker** answers `GET` and `POST /api/frame` from the record:

- it reads `etag` first, and answers 304 when it matches `If-None-Match`, without reading the 48 KB;
- otherwise it sends `frame`;
- with no record, it falls back to its SQLite row, then to 404 while there's neither.

The config page's preview and "Drawn" time read the same record.

**Trust.** This doesn't widen what Redis can do: whoever can write it can already put any text on any screen through
the providers' keys. With the frame in Redis, the renderer secret guards nothing. So steps 4 and 9 retire it, with the
claim tokens, the orchestrator and the per-screen volumes.

**A Redis restart.** The records are gone. The broker serves its SQLite copy while it still has one, or 404 with a
short sleep. The e-paper keeps its picture, and the next minute's tick writes every record again.

### Expiry: the provider's TTL is all there is

Each provider's TTL is a few multiples of its own polling interval. One missed poll doesn't blank a panel, but a dead
provider does. When a key has expired, the panel says "Not available". The notice board leaves that source's notices
out, and says "Not available" if every source it needed has expired. There's no last-good copy, per-screen state or
freshness check. The screen's own picture is the only memory.

| Key | Interval | TTL now | Change |
|---|---|---|---|
| `v1:departures:*` (TfL, Entur) | 30 s | 2 min | none |
| `v1:weather:*` | 10 min | 30 min | none |
| `v1:energy:*` (auth-broker glow-provider) | 15 min | 1 h | **about a day**, as decided for energy (step 2) |
| `v1:spotify:*` | 1 min | 3 min | none |
| `v1:spotify-top:*` | 30 min | 2 h | none |
| `v1:developer:*` | 2 min (20 s in a release) | 15 min | none |
| `v1:notices:met-office:*`, `floods:*`, `metalerts`, `tfl-stations:*`, `tfl-roads:*` | 5 min | 30 min | none |
| `v1:notices:tfl-lines:*` | 2 min | 10 min | none |
| `v1:notices:entur:*` | 5 min | 15 min | none |
| `v1:notices:bank-holidays` | 6 h | 7 days | none: gov.uk's file changes yearly, and its being down for days must change nothing |
| `v1:location:*` | 5 min | 7 days | none: a postcode doesn't move, and this is an input to the providers and the board, not drawn data |
| `v1:art:*` | renewed every minute while shown | 5 min | none |
| `v1:pace:*` | each provider cycle | 60 s | none: this is the hint's expiry (see [Pacing](#pacing)) |

**Redis being unreachable isn't expiry.** In today's renderer, a read that fails with `RedisError` keeps its last
panel, as it does now (step 2 changes only missing keys). In the pool, a render that can't reach Redis fails and
writes nothing, so the screen keeps the frame it has.

### Rotation from the clock

Each panel shows page `epoch_minute mod pages`, where `pages` is that panel's own count at that moment:

| Panel | Pages |
|---|---|
| Departures | the stops in pages of the layout's size |
| Weather | four tabs |
| Energy | three windows |
| Spotify | four or eight |

It's "the current minute" counted from the epoch, not the minute of the hour. Then a count that doesn't divide 60
(seven pages of stops, say) doesn't jump back to page 0 on the hour. When Spotify's page has nothing to show, the next
one is drawn in its place, as now.

- Every screen turns together, on the minute; that's accepted. A small fixed per-device offset can spread the reads if
  that ever matters.
- A config change doesn't start the departures on page 0 any more.
- Two renders in the same minute draw the same page, so a nudge or a retry draws what the tick would have.
- The renders on the minute only read Redis; the providers keep their own schedules, so there's no upstream load.

`share_spotify_area` already works like this (`now.minute % DEVELOPER_TURN`; 3 divides 60).

### Pacing

**One pure function in the broker** (auth-broker `services/pace.py`) decides every sleep. Nothing else computes a poll
or refresh time, and the renderer drops out of pacing altogether.

```python
def sleep_s(now: datetime, hints: Sequence[Hint]) -> int:
    """How long the screen should sleep before its next poll, from the server's clock."""
```

- **Renders** happen on the minute (the ticker), so a render boundary is every multiple of `RENDER_INTERVAL_S` (60).
- **Polls** come twice as often as renders (`POLLS_PER_RENDER = 2`). The screen sleeps until `LEAD_S` (about 3 s) after
  the next half-interval boundary, so it wakes just after a render has landed. With no hint, that's polls at about
  :03 and :33 of every minute. Out-of-cycle renders (a config save, a burst) then show within about half an interval.
  Unchanged frames cost a 304, and the extra polls flush the screen's piggybacked logs and metrics more often.
- **Hints** are the providers' `v1:pace:<source>:<id>` keys for the screen's own panels:
  - the route reads them with the frame record, in one round trip (a read, not a write, per poll), and passes them
    in as `Hint(seconds, until)`, where `until` comes from the key's TTL;
  - the function takes the shortest hint whose `until` is after `now`, never under `HINT_FLOOR_S` (20) and never over
    60, and polls at half of it, aligned to its multiples plus `LEAD_S`;
  - which keys are the screen's comes from its config (today: `v1:pace:developer:<device id>` while the developer panel
    is on), in `pace.hint_keys(config)` beside the function.
- **Network delay** never accumulates: each answer is worked out afresh from the server's clock. If the next slot is
  under `MIN_SLEEP_S` (5) away, the one after is used.
- **Before the first frame** it's the same function.

During the migration, the function also counts the legacy hint today's renderers send (`X-Refresh-Hint`, kept in the
frame's row for `PACE_LEASE_S`). So it works with every renderer deployed, and the header can go in step 9.

**The pacing component's tests:**

- a frozen clock at many offsets within the minute;
- hints shorter, longer and equal to the interval;
- the floor;
- an expired hint;
- several hints at once;
- a slot too close to now;
- that each sleep lands `LEAD_S` after a boundary.

### The pool, the queue and the render function

**The render function** is `render(device_id, now) -> Outcome`, in countdown-core. It runs to completion:

1. read `v1:config:<device id>` (none: nothing to render);
2. set the locale;
3. build the clients fresh;
4. read their keys;
5. draw;
6. hash;
7. write the frame record if the hash changed;
8. return what happened, for the metrics.

It has no sleeps, no loops beyond its panels, and no state kept between calls except read-only caches (fonts,
translations, the Redis connection). The pairing screen is one of its outcomes (see
[Decided here](#decided-here-not-in-the-answers)).

**The worker** (`countdown-render-worker`, the renderer's image with another command, a compose service on the host)
is a thin wrapper:

```text
loop: BRPOP v1:render:jobs → claim → render(device_id, now) → release → metrics
```

It runs one render at a time per process, because the locale is a module global. It has a deadline per render
(`faulthandler.dump_traceback_later(20, exit=True)`); compose restarts a worker that hits it. There are two worker
processes to start with.

**The queue** is three small scripts, each atomic in Redis, over these keys (each with the device id):

| Key | Kind | What it's for |
|---|---|---|
| `v1:render:jobs` | list | the job queue |
| `v1:render:queued:<id>` | flag | a job for the screen is in the queue |
| `v1:render:lease:<id>` | string with a token, 30 s TTL | a render for the screen is in flight |
| `v1:render:again:<id>` | flag, 30 s TTL | a job came while one was in flight |

- **enqueue(id):** `SET queued NX`; only if that set it, `LPUSH jobs id`. So a screen is in the queue at most once,
  however many triggers fire.
- **claim(id), after the BRPOP:** `DEL queued`; then `SET lease token NX PX 30000`. If the lease is taken, `SET again`
  instead, and the job is done.
- **release(id):** if the lease is still this worker's token, `DEL lease`; if `DEL again` removed one, `enqueue(id)`.

The guarantees:

- At most one render per screen is in flight.
- A job that arrives during a render, from a nudge or a tick, leaves **exactly one** follow-up render: claim and
  release are atomic over the same keys, so either the job's claim sees the lease and flags `again` before the release
  checks it, or it runs after the release and renders itself.
- A worker that dies mid-render loses its job and its follow-up. The lease and the flag lapse in 30 s, and the next
  minute's tick covers it.
- A Redis restart loses the queue; the next tick refills it.

**The ticker** (`countdown-render-ticker`, one small process) pushes `enqueue(id)` for every screen the pool renders,
on every minute. Its sleep-until-the-minute loop is the only clock on the render side. Running two by mistake is
harmless: enqueue coalesces.

**Which screens the pool renders:** `v1:render:screens`, a hash of device id to slug (the slug names the screen in
metrics). During the migration the orchestrator writes it for the screens it has moved to the pool. At the end the
broker writes it, as it writes the configs. A worker drops any job for a device not listed, so a screen that still has
its own container is never drawn twice.

### Config saves and bursts push jobs

- **A config save** pushes `enqueue(device id)` for that screen, after writing its config. The new frame is drawn at
  once, and shows at the screen's next poll, within about half an interval. That replaces the `v1:config:changed`
  channel for the pool. The channel stays as long as any screen has its own renderer, and goes in step 9.
- **A burst.** When a provider's data turns bursty (the developer provider when a release starts, and on each cycle
  while it runs), it pushes `enqueue(id)` for the screens subscribed to it, and writes its pace key as now.
  "Subscribed" comes from the existing subscription sets: `v1:sub:developer` lists device ids already. A provider keyed
  by stop or place would need to map ids to screens first; none needs to yet.
- **Out-of-cycle renders** follow the providers' own epoch-aligned cycles (20 s for the developer one), and the pacing
  function's hint makes the screen poll at half that.

### Observability

**Metrics** (OTLP to the host's Fluent Bit, from the worker), each with `device` (the slug) and, where it's per
render, `outcome`:

| Metric | Kind | What it shows |
|---|---|---|
| `countdown.render.duration` | histogram | how long each render took |
| `countdown.render.count` | counter | `outcome` is `written`, `unchanged` (frames skipped because nothing changed) or `failed` |
| `countdown.render.last_success` | gauge, Unix time | when the screen last got a good frame. The one that shows a single stuck screen: alert when it's more than a few minutes old |
| `countdown.render.expired_panels` | gauge | panels drawn "Not available" because their key expired, with `panel` |
| `countdown.render.lag` and `countdown.render.queued` | histogram, gauge | how long jobs wait, and how many are waiting: the pool's capacity signals |

- **The existing gauges** (`countdown.api.status`, `countdown.problem`, `countdown.stage`) move from the resource's
  `host.name` to the `device` attribute, since a worker's `host.name` is the worker.
- **The dashboards** in auth-broker are updated to match.
- **Logs** are for what goes wrong only:
  - a render that fails, with its traceback;
  - Redis unreachable;
  - a job for an unknown device;
  - a worker past its deadline.

  There's no line per frame. As now, nothing logged names a postcode, place or URL (`countdown_core.core.log_format`).

### Failure

| What fails | What happens |
|---|---|
| A panel's key has expired | "Not available" in that area; counted in `expired_panels`; not a failed render |
| The render raises | the job fails (logged, counted, `last_success` doesn't move); the next tick tries again; the screen keeps its frame |
| A render hangs | the deadline ends the worker, compose restarts it, the lease lapses, the next tick re-renders |
| One screen crashes its worker every time | its `last_success` stops while the rest of the pool carries on, since every job is separate |
| Redis is down | nothing renders, the broker serves its SQLite copy or the record it last read, and the screen keeps its picture |
| The broker is down | the screen can't poll; renders carry on into Redis |

### Capacity

The host is the Oracle ARM server. Measured on a 2-vCPU x86 sandbox (an order of magnitude only; to be re-measured on
the host):

| | Cost |
|---|---|
| A warm render, clients built fresh | about 50 ms of CPU, p95 about 70 ms |
| A process importing the renderer, before its first render | 0.6 s of CPU |
| A process, resident | about 115 MB |

So one worker process manages about 800 renders a minute flat out, against today's 115 MB and one container per
screen. A render reads 10 to 15 keys, and at most writes one record.

**The burst on the minute** is the real limit, not the throughput. Every screen is enqueued at :00, and its frame
should land before the poll at :03 (`LEAD_S`). Two workers at 75 ms a render finish 80 screens in those 3 s. Beyond
that, three things help, in order of cost:

1. more workers;
2. a larger `LEAD_S`;
3. the per-device offset.

## Migration

Nikolai merges and releases as soon as a PR is green, so **every step below is safe to merge and release on its own,
in any order, against whatever is deployed**. Where a change truly needs something else deployed first, it's split
off as a draft PR titled "[after … deployed]".

**1. Rotation from the clock** (pi-dashboard).

- **Change:** departures, weather, energy and Spotify pick their page from `epoch_minute mod pages`
  (`core/rotation.py`), in place of their counters. A config change no longer restarts the departures at page 0.
- **Safe alone:** it's internal to the renderer.
- **Rollback:** revert the release.

**2. Expired means "Not available"** (pi-dashboard; auth-broker for the energy TTL).

- **Change:**
  - A client whose key is missing returns a "Not available" panel instead of raising to keep its last one.
  - The notice board drops `STALE_AFTER` and `last_tried`: an expired source adds nothing.
  - The weather reads the store on every poll.
  - `DeveloperNoticeSource` reads `v1:developer:<id>` itself.
  - A `RedisError` still keeps the last panel.
  - In auth-broker, glow-provider's TTL goes to about a day.
- **Safe alone:** each half is internal; a longer TTL only means the energy panel survives a longer Glowmarkt outage.
- **Rollback:** revert either.

**3. The pacing function** (auth-broker).

- **Change:** `services/pace.py` becomes the one pure `sleep_s(now, hints)`, as above. `GET` and `POST /api/frame`
  answer with it. The hints are read from the screen's `v1:pace:` keys, plus the legacy `X-Refresh-Hint` lease from
  today's renderers.
- **Safe alone:** the ESP32 sleeps whatever it's told. Polls double, to about two a minute per screen, with no Redis
  write.
- **Rollback:** revert the release.

**4. The frame record** (pi-dashboard and auth-broker, each a PR that's safe alone).

- **pi-dashboard:** today's renderer writes `v1:frame:<device id>` when the hash changes, as well as PUTting. The
  record replaces `render_state.RenderState`, `needs_put` and `RenderStateStore` (`frame_etag` stays).
- **auth-broker:** serves the record when it's there and newer than the SQLite row, else the row.
- **Safe alone:** an old broker ignores the record, and a new broker with no record serves the row.
- **Rollback:** revert either. The broker then serves SQLite, which today's renderers still write.

**5. The render function** (pi-dashboard).

- **Change:**
  - Extract `render(device_id, now)` from `DisplayLoop`, `ApiRegistry` and `DisplayController`.
  - Today's per-screen loop calls it on the minute, on the config nudge (`ConfigWatcher`), and every 20 s while a pace
    key is live. It still PUTs alongside the record, and still reads `GET /api/config` for a pairing code.
  - Clients are built fresh each render; their status goes to the metrics.
- **Safe alone:** the same screens, the same frames, now on the minute.
- **Rollback:** revert the release.

**6. The pool, one screen first** (pi-dashboard; auth-broker for one additive field).

- **pi-dashboard adds:**
  - the `render-worker` and `render-ticker` services in `server/deploy/docker-compose.yml`, with the queue and its
    scripts, tested through the real entry points the way CI starts the providers;
  - the metrics, with the `device` attribute;
  - `POOL_SLUGS` in the orchestrator. For each of those screens it stops the container (keeping it and its volumes),
    and writes the screen into `v1:render:screens` on every pass.
- **auth-broker adds:** `device_id` in `GET /api/orchestrator/assignments` (additive). Without it the orchestrator
  logs and leaves that screen on its container.
- **Canary:** Nikolai's own screen first, for a few days, then all.
- **Safe alone:** with `POOL_SLUGS` empty nothing changes, and a worker drops jobs for screens not listed.
- **Rollback:** take the slug out of `POOL_SLUGS`. The orchestrator removes it from the hash, waits for its lease to
  lapse, and starts its container again.

**7. Jobs from the broker and the providers** (auth-broker, and pi-dashboard where a provider there bursts).

- **Change:**
  - A config save pushes `enqueue(device id)`, alongside the `v1:config:changed` publish the per-screen renderers
    still need.
  - The developer provider pushes jobs for its devices on each burst cycle.
  - The broker writes the pairing key (see below) and pushes a job when it issues a code.
- **Safe alone:** a job for a screen the pool doesn't render is dropped, and the publish carries on.
- **Rollback:** revert.

**8. The dashboards** (auth-broker). The panels read the new `device`-attributed series beside the old ones. Safe at
any time.

**9. Retire the per-screen path** (both repos). Every change here waits for every screen to be on the pool, so each
is a draft PR titled "[after every screen is pooled]".

- **pi-dashboard:**
  - the orchestrator removes the containers, and their volumes a few weeks later;
  - the code goes: `DisplayLoop`, `ServerTarget`, `ConfigWatcher`, `BrokerClient`'s `GET /api/config` fallback, the
    claim (`RendererRegistrar`), `DisplayController`'s partial-refresh logic, `ApiRegistry.pace` and
    `AbstractClient.pace_keys`, and the per-process `OtlpPublisher` gauges.
- **auth-broker:**
  - the broker writes `v1:render:screens` from its paired devices, in place of the orchestrator;
  - the `v1:config:changed` publish goes (the job queue has replaced it for every screen by then);
  - `PUT /api/frame`, the frames table, the `X-Refresh-Hint` lease and `PACE_LEASE_S` go;
  - the claim flow goes (`/api/renderers/claim`, claim tokens);
  - the orchestrator's endpoints and token go.
- **Then:** the orchestrator itself, with the `countdown-orchestrator` image, once the broker writes the screens.
- **Rollback:** up to the deletion of the volumes, put the slugs back on containers. After it, a screen needs a new
  renderer assignment.

**When the channel and the per-screen path go.** The job queue replaces `v1:config:changed` from step 7, for every
screen the pool renders. The channel's publisher and `ConfigWatcher`, and the per-screen renderer path with them, are
removed in step 9, once no screen has its own container.

## Decided here, not in the answers

These follow from the decisions but weren't asked about; say if any should go another way.

- **The pairing screen.** The config copy in the store deliberately carries no pairing code. So in the pool, the
  broker writes the live code to `v1:pairing:<device id>` (its TTL the code's own 10 minutes) and pushes a job, and the
  render draws the pairing screen while the key exists.
  - A Redis reader could then see a live code. That's the same trust as the config, and the firmware never asks for a
    code itself: it's only the fallback for handing a screen to a new owner.
  - Until step 7, a pooled screen with a code requested keeps showing its dashboard.
  - **Confirmed by Nikolai (2026-10-18):** the live code goes in Redis, accepting that every Redis reader on the host
    can see it for those 10 minutes. Rejected: the broker drawing the pairing screen (he'd rather the broker draw no
    screens); the worker fetching the code from the broker at render time (it needs a pool-wide broker token, and the
    rendered frame still lands in `v1:frame:<device id>` with the code on it); encrypting the code for the workers
    (gives the workers a secret); revealing it digit by digit with partial refreshes (a reader watching the frame key
    still collects every digit). The config copy still never carries the code.
- **Hints by key, read by the broker.** The broker reads the screen's `v1:pace:` keys itself, so the minimum, the floor
  and the expiry live in its one function. The render side never computes an interval.
- **Bursts come from the provider's cycle.** The ticker only ticks on the minute.
- **No `v1:render-state` key.** The frame record's `etag` is the only state a render needs.
- **Epoch minutes, not minute of the hour.** So a page count that doesn't divide 60 doesn't jump on the hour.
- **A `RedisError` keeps today's renderer's last panel** in step 2. Only an expired key is "not available".
- **Numbers to settle in the step that builds each:**

  | Setting | Value |
  |---|---|
  | `LEAD_S` | 3 |
  | `HINT_FLOOR_S` | 20 |
  | `MIN_SLEEP_S` | 5 |
  | the lease | 30 s |
  | the render deadline | 20 s |
  | worker processes | 2 |
