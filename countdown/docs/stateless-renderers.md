# How rendering works

A render runs to completion and keeps nothing: read Redis, build the frame, write it, done. A small pool of warm
workers renders a screen whenever a job for it arrives on a Redis queue. Services share only Redis; the keys are
documented in [countdown-api](https://github.com/nvorkinn/countdown-api#readme), and the broker's side (serving frames,
pacing) in [countdown-server](https://github.com/nvorkinn/countdown-server#readme).

## Who renders

`worker-1`, `worker-2` and `ticker` (containers `renderer-worker-1-1`, `renderer-worker-2-1` and `renderer-ticker-1`): compose services in `renderer/deploy/docker-compose.renderer.yml`, from
the `countdown-renderer` image, on the host's network (`renderer/src/countdown_renderer/render_pool.py`, drawing with
`core/`). They draw every screen in the hash `v1:render:screens`, which countdown-server writes for every paired device (its
values are the screens' slugs, which name them in metrics). A job for any other device is dropped.

A worker blocks on the job queue and calls `render(device_id, now)` (`countdown_core/render.py`) for each job, one at a
time (the locale is a module global). A render that passes 20 s ends the worker (`faulthandler`), and compose restarts
it. The only state kept between renders is read-only: fonts, translations and Redis connections.

## What queues a render

A job is `enqueue(device id)` (`store/render_queue.py`), from:

- the ticker, for every screen, every minute on the minute (running two is harmless);
- the broker, when a config is saved or a pairing code is issued;
- a provider whose data turns bursty (the developer provider, on each cycle while a release runs), for the screens
  subscribed to it.

Three atomic Lua scripts over `v1:render:*` keys give two guarantees. A screen is queued at most once however many
triggers fire (`queued` flag, 60 s). At most one render per screen is in flight (`lease`, 30 s), and a job that arrives
meanwhile leaves exactly one follow-up (`again` flag, 30 s). A worker that dies loses its job, but the lease and flags
lapse and the next tick covers it; a Redis restart loses the queue and the next tick refills it.

## The render

`render(device_id, now) -> Outcome` reads `v1:pairing:<device id>` if a pairing code is live (and draws the pairing
screen), else `v1:config:<device id>`. It sets the locale, builds the API clients fresh, reads their keys, draws as of
`now`, and writes `v1:frame:<device id>` if the frame's hash changed. It returns an `Outcome` for the metrics. Every
panel goes by `now`, so the same inputs draw the same frame.

## Frames through Redis

`v1:frame:<device id>` is a hash (`store/frame_record.py`):

- `frame`: the 48,000 raw bytes the panel takes;
- `etag`: the frame's BLAKE2b hash in hex, which the broker uses as its ETag;
- `rendered_at`: when the picture was first written, ISO 8601 in UTC.

A render compares its hash with `etag` and writes only if it differs, so an unchanged frame costs one small read. The
record has a TTL of a day that a skipped write doesn't renew: under the Redis `volatile-lru` policy only keys with a
TTL can be evicted, and a frame is the biggest value in the store. A picture unchanged all day is therefore written
again by the first render after the record lapses. The broker serves the record and answers 304 on a matching
`If-None-Match`; the e-paper keeps its picture if there's no record.

## Expiry

Each provider's TTL is the only expiry: a few multiples of its own polling interval, so one missed poll blanks
nothing but a dead provider does. A panel whose key has expired draws "Not available"; the notice board leaves that
source's notices out, and says "Not available" if every source it needed has expired. Nothing older than a TTL is drawn,
and no last-good copy is kept.

## Rotation

Each rotating panel (the stops, the weather's tabs, the energy windows, the Spotify area) shows page
`epoch minute mod its page count` (`core/rotation.py`). Every screen turns its pages together, on the minute; two
renders in one minute draw the same page; a config change carries on at the clock's page. The minute is counted from
the epoch, not the hour, so a page count that doesn't divide 60 doesn't jump back on the hour. When Spotify's page has
nothing to show, the next is drawn.

## Pacing

How long a screen sleeps between polls is decided by the broker alone, from its own clock: just after the next
half-render boundary (about twice per render), and sooner while a provider's `v1:pace:<source>:<id>` key is live. A
provider with bursty data writes that key with a short TTL, holding the seconds it wants. Rendering computes no
interval.

## Failure

| What fails | What happens |
|---|---|
| A panel's key has expired | "Not available" in that area, counted in `expired_panels`; the render succeeds |
| Redis can't be read, or the config doesn't validate | The render fails and writes nothing; the screen keeps its frame, and the next tick tries again |
| A render hangs | The deadline ends the worker, compose restarts it, the lease lapses |
| One screen crashes its worker every time | Only its `last_success` stops; every job is separate |
| Redis is down | Nothing renders; the screen keeps its picture |
| The broker is down | The screen can't poll; renders carry on into Redis |

## Metrics and logs

Workers send OTLP metrics (`renderer/src/countdown_renderer/pool_metrics.py`), each with `device` (the screen's slug):
`countdown.render.duration`, `.count` (`outcome`: `written`, `unchanged` or `failed`), `.last_success` (the one to alert
on for a stuck screen), `.expired_panels`, `.lag` and `.queued` (the pool's capacity signals). Logs are for what goes
wrong only, with no line per frame, and nothing logged names a postcode, place or URL (`core/log_format.py`).
