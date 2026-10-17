# countdown-providers

One long-running process per API (TfL, Entur, weather, ...) that fetches what the screens want and writes it to a shared
Redis store, so renderers can read it from there instead of each calling every API itself. Ten screens watching the
same bus stop then cost one TfL call, not ten.

**Status: scaffolding.** The TfL, Entur and weather providers fetch, the broker writes the subscriptions (auth-broker,
on every config save), and renderers read the store only once they're told to (step 4 of
[Enabling in production](#enabling-in-production)). In production the services are
behind a Compose profile, so they aren't started (see [Enabling in production](#enabling-in-production)).

## The model

```
auth-broker ──SADD (replace)──▶ v1:sub:tfl ──SMEMBERS──▶ provider-tfl ──SET … EX ttl──▶ v1:departures:<stop>
            ──PUBLISH───────▶ v1:sub:changed ──wakes──▶                                      │
                                                                                 renderers (later) ◀──GET
```

- The **broker** writes, for each source, the set of ids some screen wants, replacing it atomically whenever a config
  is saved, then publishes on `v1:sub:changed` as a nudge.
- Each **provider** runs the same loop (`runner.ProviderRunner`): read its set, fetch what's in its shard, write each
  result with a TTL, then sleep until the next tick of its interval (epoch-aligned), or until the nudge or a shutdown.
- A **renderer** will read the keys. A stop with nothing due is still written (an empty list), so "no buses" isn't
  "no data"; a **missing key** means the provider is down (its keys expired), and the panel is hidden.

The key names are in `countdown_core/store/keys.py` and the stored models in `countdown_core/store/models.py`, in
countdown-core so the providers and renderers share them.

| Key | Type | Written by | Holds |
|---|---|---|---|
| `v1:sub:tfl` | set | broker | TfL NaPTAN stop ids, e.g. `490000123W` |
| `v1:sub:entur` | set | broker | Entur stop place or quay ids, e.g. `NSR:StopPlace:58366`, `NSR:Quay:7333` |
| `v1:sub:weather` | set | broker | location keys, e.g. `london-gb-2643743` |
| `v1:sub:glow` | set | broker | Glow device ids |
| `v1:sub:spotify` | set | broker | device ids |
| `v1:sub:changed` | pub/sub channel | broker | empty message after replacing subscriptions |
| `v1:departures:<stop id>` | string, TTL ~2 min | provider-tfl, provider-entur | `StopDepartures` JSON |
| `v1:weather:<key>` | string, TTL ~30 min | provider-weather | `StoredForecast` JSON (Open-Meteo's forecast; the renderer makes the pages) |
| `v1:energy:<device id>` | string, TTL ~1 h | auth-broker's glow-provider | `StoredEnergy` JSON (`store/energy.py`): Glowmarkt's own readings answers (`hourly`, `daily`, `monthly`) with the `offset` they were asked at; see [Energy](#energy) |
| `v1:spotify:<device id>` | string, TTL 3 min | auth-broker's `spotify-provider`, every minute | `StoredPlayer` JSON (`store/music.py`): each service's queue, as the broker's `/queue` route answers it |
| `v1:spotify-top:<device id>` | string, TTL 2 h | auth-broker's `spotify-provider`, every 30 min | `StoredTop` JSON (`store/music.py`): each service's top tracks and artists by time range |

The music keys are written by a provider in auth-broker, not here: it calls Spotify and Apple Music with the broker's
own tokens, which never leave it (auth-broker's README, "Music in the store"). As the two repositories can't share
code, each service's part is exactly what the broker's proxy route answers a renderer (`/api/spotify/queue`,
`/api/apple-music/top/tracks?time_range=long_term&limit=5`, ...), read with the models the renderer already has
(`spotify/models.py`); only the wrappers in `store/music.py` are new. Both repositories' tests read the same examples
(`tests/fixtures/music_*.json`), so keep them in step:

```json
{"fetched_at": "2026-10-17T12:00:00+00:00", "spotify": {"currently_playing": {...}, "queue": [...]}, "apple_music": null}
{"fetched_at": "...", "spotify": {"long_term": {"tracks": {"items": [...]}, "artists": {"items": [...]}}, "short_term": {...}}, "apple_music": null}
```

A part is `null` when the service is off for the device or couldn't be fetched (the renderer then keeps its last
panel); nothing playing is a queue with `"currently_playing": null`.

TfL and Entur write the same `StopDepartures` into the same `v1:departures:` namespace (their ids never clash), so the
transit panel needn't know the country. Entur's also fill the fields only it has: the stop's `modes` (a stop can serve
several, e.g. metro and tram, each an icon in its header), each departure's `mode` (its row's icon) and `platform` (the
quay it leaves from); a quay's own code is its `stop_letter`. TfL leaves them unset. Every stored model has
`fetched_at`.

Failures stay small: a batch whose fetch raises is logged and skipped (its keys expire if it keeps failing), an id the
provider couldn't fetch is just left out, and an unreachable Redis is logged and retried on the next tick. A missed
nudge only delays a change until the next tick, as every cycle re-reads the subscriptions.

### Energy

The energy panel's readings are written by auth-broker, not by a provider here: its `glow-provider` (the broker's image
with another command) signs in to Glowmarkt with the passwords the broker keeps encrypted, so they never leave it. It
can't import countdown-core, so what it stores is Glowmarkt's answers as they came, in a thin wrapper, and the renderer
reads them with the models it reads Glowmarkt with (`glow/models.py`):

```json
{"fetched_at": "2026-09-25T05:10:00+00:00", "device_id": "a1b2c3d4e5f6", "offset": "-60", "resource_id": "...",
 "hourly": {"data": [[1790229600, 0.21], ...], ...}, "daily": {...}, "monthly": {...}}
```

`hourly`, `daily` and `monthly` are Glowmarkt's `PT1H`, `P1D` and `P1M` readings, each asked for, as `GlowClient` would
ask, from the start of its page's window (the hour 24 hours before, midnight 31 days before, 1st January) to
`fetched_at`, in the screen's time zone at its offset then. `offset` is Glowmarkt's parameter for that (minutes from
local time to UTC, so BST is `-60`): Glowmarkt labels each bucket with its local wall-clock start as if it were UTC, and
the renderer relabels them at that offset. It cuts each page to its window as of when it draws, so a value up to its
TTL old still gives the right 24 hours, 31 days and year, short only of the readings since `fetched_at`. The broker
fetches every 15 minutes, and keeps each value for an hour. `tests/fixtures/glow_energy.json` is one fetch, from
Glowmarkt's answers to the stored value, and auth-broker's tests read the same file: change both together.

## Running locally

```sh
docker run --rm -p 127.0.0.1:6379:6379 redis:8.10-alpine
redis-cli SADD v1:sub:tfl 490000123W           # a stop to fetch
uv run countdown-provider-tfl                   # from countdown/; set TFL_APP_KEY to avoid TfL's tighter limits
redis-cli GET v1:departures:490000123W
redis-cli TTL v1:departures:490000123W
redis-cli PUBLISH v1:sub:changed ""             # after changing the set, to fetch at once rather than next tick
```

Logs are JSON lines on stderr; `LOG_LEVEL=debug` shows each cycle.

### Settings

| Variable | Default | |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | |
| `REDIS_PASSWORD` | none | kept out of the URL so the secret needn't be spliced into one |
| `SHARD_INDEX`, `SHARD_COUNT` | `0`, `1` | see [Sharding](#sharding) |
| `TFL_APP_KEY` | none | TfL only |
| `LOG_LEVEL` | `info` | |

## Sharding

To split one provider's ids between several copies, run `SHARD_COUNT` copies with `SHARD_INDEX` from `0` to
`SHARD_COUNT - 1`. Each id goes to `sha256(id) % SHARD_COUNT` (`sharding.shard_of`), the same in every process, so
every id is fetched by exactly one copy. (Not Python's `hash()`, which differs from process to process.) Changing
`SHARD_COUNT` moves most ids to another copy; that's harmless, as each copy just starts fetching its new share.

## Adding a provider

1. Add its stored model to `countdown_core/store/models.py` (subclass `Stored`, which carries `fetched_at`) and, if it's
   new, its key builder to `keys.py`.
2. Add `countdown_providers/<name>/main.py` with a class matching the `Provider` protocol (`provider.py`): `name` (the
   `v1:sub:<name>` set it reads), `ttl_s`, `interval_s`, `output_key(id)` and `async fetch(ids)`. `fetch` returns a
   model per id it could fetch and leaves out (and logs) the rest rather than raising. Reuse the renderer's client from
   countdown-core where there is one; a synchronous one runs in `asyncio.to_thread`, as `tfl/main.py` does.
3. Give it a `main()` that builds it and calls `asyncio.run(serve(provider, Settings.from_env()))`, and add it to
   `[project.scripts]` in `pyproject.toml` as `countdown-provider-<name>`.
4. Add a `provider-<name>` service to `../server/deploy/docker-compose.yml` like the others, with its `command`.
5. Test it with its API mocked; `tests/test_provider_runner.py` already covers the loop.

`weather/main.py` is the smallest complete example of the shape. `tfl/main.py` and `entur/main.py` are the same
shape for departures, each wrapping the renderer's own source (`TflSource`, `EnturSource`). Entur, like Open-Meteo,
needs no key, only the `ET-Client-Name` header its client already sends. Location keys there are the broker's
`<name>-<country>-<GeoNames id>` (`london-gb-2643743`): the id finds the place exactly, and a key without one (a
screen saved before the place picker, until it's backfilled) is geocoded by name. Open-Meteo is keyless, so it
needs no secret.

## The image

Every provider is in one image, `ghcr.io/nvorkinn/countdown-providers:<tag>`, built by each release from its own
wheels and pinned to the release's version by the deploy, like the orchestrator. Its default command is the TfL
provider; a service picks another with `command`. To build it locally, from `countdown/`:

```sh
uv build --wheel --all-packages
docker build -f providers/Dockerfile -t countdown-providers:dev .
```

## Enabling in production

The services are in `../server/deploy/docker-compose.yml`, under the `providers` profile, so `deploy.sh`'s plain
`docker compose up` neither starts nor removes them. To turn them on:

1. Have the broker write subscriptions (auth-broker) and Redis running on the host (`127.0.0.1:6379`; the services use
   host networking to reach it).
2. Add the secrets `REDIS_PASSWORD` and `TFL_APP_KEY` to the repo, and pass them to `deploy.sh` in `release.yml`'s
   `deploy-renderers` job, next to `ORCHESTRATOR_TOKEN`. Once they're always set, consider `:?` instead of `:-` in the
   compose file, so a missing one fails the deploy rather than starting a provider that can't log in.
3. Remove the `profiles: [ providers ]` lines from the services that should run. Then every deploy starts and
   upgrades them, and `--wait` fails the deploy if one won't stay up.

4. Once the providers are filling the store, point the renderers at it: set `RENDERER_REDIS_URL` to
   `redis://127.0.0.1:6379/0` for the orchestrator (with `REDIS_PASSWORD`, as in step 2). It passes them to each
   renderer as `REDIS_URL` and `REDIS_PASSWORD`, and remakes the running ones (keeping their volumes), because a change
   to the store they read counts as an upgrade, like a new image. Those renderers then read TfL's and Entur's
   departures (`v1:departures:<stop>`) and the weather (`v1:weather:<location>`) from the store, and stop calling TfL,
   Entur and Open-Meteo for them; only the disruption notices still come straight from their APIs. The energy panel's
   readings (`v1:energy:<device id>`) come from the store too, from auth-broker's `glow-provider`, so start that first
   (see "The energy provider" in auth-broker's README): without it, every energy panel says "Could not connect", as
   the renderers are remade with the store and have no readings to draw. Unset it again to go back. Renderers on
   standalone Pis never get it, so they keep calling the APIs.

   The Spotify area (Spotify and Apple Music) comes from the store too (`v1:spotify:<device>` and
   `v1:spotify-top:<device>`, under the renderer's own device id) instead of the broker's proxy routes, so turn on
   auth-broker's `spotify-provider` first (its README, "Music in the store"): while its keys are missing, the area
   keeps its last panel.

   A key a renderer wants that isn't in the store (its provider is down, or hasn't got to it yet) is skipped: that stop
   is left off, or the weather keeps its last forecast, as when an API call fails.

Until then they can be tried on the host with `docker compose --profile providers up -d`. Containers started that way
are left alone by later deploys: not removed, but not upgraded either.
