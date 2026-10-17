# countdown-providers

One long-running process per API (TfL, weather, ...) that fetches what the screens want and writes it to a shared
Redis store, so renderers can read it from there instead of each calling every API itself. Ten screens watching the
same bus stop then cost one TfL call, not ten.

**Status: scaffolding.** The TfL and weather providers fetch, but nothing writes subscriptions yet
(that's the broker's side, in auth-broker), and renderers don't read the store yet. In production the services are
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
| `v1:sub:entur` | set | broker | Entur stop ids, e.g. `NSR:StopPlace:58366` |
| `v1:sub:weather` | set | broker | location keys, e.g. `london-gb-2643743` |
| `v1:sub:glow` | set | broker | Glow device ids |
| `v1:sub:spotify` | set | broker | device ids |
| `v1:sub:changed` | pub/sub channel | broker | empty message after replacing subscriptions |
| `v1:departures:<stop id>` | string, TTL ~2 min | provider-tfl (and later Entur) | `StopDepartures` JSON |
| `v1:weather:<key>` | string, TTL ~30 min | provider-weather | `StoredForecast` JSON (Open-Meteo's forecast; the renderer makes the pages) |
| `v1:energy:<device id>` | string, TTL ~1 h | (provider to come) | (model to come) |
| `v1:spotify:<device id>` | string, TTL ~1 min | (provider to come) | (model to come) |

TfL and Entur write the same `StopDepartures` into the same `v1:departures:` namespace (their ids never clash), so the
transit panel needn't know the country. Every stored model has `fetched_at`.

Failures stay small: a batch whose fetch raises is logged and skipped (its keys expire if it keeps failing), an id the
provider couldn't fetch is just left out, and an unreachable Redis is logged and retried on the next tick. A missed
nudge only delays a change until the next tick, as every cycle re-reads the subscriptions.

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

`weather/main.py` is the smallest complete example of the shape. Location keys there are the broker's
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

Until then they can be tried on the host with `docker compose --profile providers up -d`. Containers started that way
are left alone by later deploys: not removed, but not upgraded either.
