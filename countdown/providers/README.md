# countdown-providers

One long-running process per API (TfL, Entur, weather...) that fetches what the screens want and writes it to the
shared Redis store, which the render pool reads. Ten screens watching one bus stop cost one TfL call, not ten.
auth-broker's own providers write the energy, music and developer panels' keys; see its
[README](https://github.com/nvorkinn/auth-broker#readme). The keys and their models are the store contract, listed in
[countdown-api](https://github.com/nvorkinn/countdown-api#readme).

## The model

- The **broker** writes, for each source, the set of ids some screen wants (`v1:sub:<source>`), replacing it atomically
  on every config save, then publishes on `v1:sub:changed` as a nudge.
- Each **provider** runs the same loop (`runner.ProviderRunner`): read its set, fetch its shard's ids in batches, write
  each result with a TTL, then sleep until the next tick of its interval (epoch-aligned), or until a nudge or shutdown.
- The **render pool** reads the keys. A stop with nothing due is still written (an empty list), so "no buses" isn't
  "no data". A missing key means the provider is down, and the panel says "Not available".

Failures stay small: a batch whose fetch raises is logged and skipped (its keys expire if it keeps failing), an id the
provider couldn't fetch is left out, and an unreachable Redis is retried on the next tick. A missed nudge only delays a
change until the next tick.

Stored models are in `countdown_core/store/models.py` and the key builders in `store/keys.py`, in countdown-core so the
providers and the render pool share them.

## The providers

Each has a console script `countdown-provider-<name>` and a compose service `provider-<name>`.

| Provider | Reads | Writes (TTL) | Every |
|---|---|---|---|
| `tfl` | `v1:sub:tfl` | `v1:departures:<stop>` (2 min) | 30 s |
| `entur` | `v1:sub:entur` | `v1:departures:<stop>` (2 min) | 30 s |
| `weather` | `v1:sub:weather` | `v1:weather:<key>` (30 min) | 10 min |
| `tfl-notices` | `v1:sub:tfl`, `v1:sub:postcode` | `v1:notices:tfl-lines:<stop>` (10 min), `tfl-stations:<stop>` and `tfl-roads:<place>` (30 min) | 2 min, 5 min |
| `entur-situations` | `v1:sub:entur` | `v1:notices:entur:<stop>` (15 min) | 5 min |
| `met-office` | `v1:sub:postcode`, `v1:location:*` | `v1:notices:met-office:<place>` (30 min) | 5 min |
| `floods` | `v1:sub:postcode`, `v1:location:*` | `v1:notices:floods:<place>` (30 min) | 5 min |
| `metalerts` | `v1:sub:postcode` | `v1:notices:metalerts` (30 min) | 5 min |
| `bank-holidays` | nothing (`fixed_ids`) | `v1:notices:bank-holidays` (7 days) | 6 h |
| `locations` | `v1:sub:postcode`, `v1:sub:weather` | `v1:location:<place>` (7 days) | 5 min |

`<place>` is `keys.place(postcode)`, e.g. `SE173LL`. What each stores, as it came from upstream, so the render pool
makes the notices when it draws, in the screen's language:

- **Departures.** TfL and Entur write the same `StopDepartures` into one namespace. Entur's also fill `modes`, each
  departure's `mode` and `platform`. Entur needs no key, only the `ET-Client-Name` header, and allows 1000 non-trip
  requests a minute to a named client, shared by `entur` and `entur-situations`; after a 429 the latter pauses for a
  minute.
- **Weather.** The broker subscribes each screen's place as `<name>-<country>-<GeoNames id>` (the config's
  `weather.key`), so screens sharing a place share one Open-Meteo forecast. A key without an id is typed text,
  geocoded by name; a UK postcode or district slug (`sw1a-1aa`, `se17`) is placed by `locations` instead.
- **TfL notices.** One process, three providers (`runner.serve_all`): line status (each stop's lines are looked up once
  a day, then every line is asked about in one request), station disruptions, and `/Road/all/Disruption` fetched once
  and cut to 3 km around each UK postcode.
- **Met Office, floods, MetAlerts.** The Met Office feed is fetched once per region per cycle and written under each
  UK postcode in it. Floods fetches the Environment Agency's list once and gives each UK postcode the warnings for flood
  areas within 5 km. MetAlerts is one key for all of Norway, fetched only while a Norwegian (four-digit) postcode is
  subscribed, with `If-Modified-Since` and MET's required `User-Agent`.
- **Bank holidays.** gov.uk's one file for every division, kept a week; an answer missing a division isn't written
  over a good one.
- **Locations.** The one process that looks postcodes up (postcodes.io for UK, Kartverket for Norway), so every other
  reader places a postcode only from the store, via `locations/reader.py`'s `LocationReader`. Each postcode is looked up
  once a day and a new one at once; one neither service knows is written as not found. A place that is new or has moved
  is announced on `v1:location:changed`, which wakes the providers that read it (`wakes_on`) so their notices follow
  within seconds. A second runner in the same process places the postcodes that weather keys stand for.
- **Pace.** A provider with bursty data defines `pace(item_id, fetched)` (`provider.py`); the runner writes
  `v1:pace:<name>:<id>` with a short TTL, which auth-broker reads to poll sooner.

Nothing logged says where a screen is: no postcode, place or coordinates, nor a URL with one in it. Failures are
logged by reason (`log_format.error_reason`: the error's type and an HTTP status), never its text, which can quote a URL
(TfL's has the app key). The runner names a failed batch only for stops (`runner.NAMED_SOURCES`). urllib3's retry
warnings are silenced by `configure_logging`.

## Running locally

```sh
docker run --rm -p 127.0.0.1:6379:6379 redis:8.10-alpine
redis-cli SADD v1:sub:tfl 490000123W           # a stop to fetch
uv run countdown-provider-tfl                   # from countdown/; set TFL_APP_KEY to avoid TfL's tighter limits
redis-cli GET v1:departures:490000123W
redis-cli PUBLISH v1:sub:changed ""             # after changing the set, to fetch at once rather than next tick
```

Logs are JSON lines on stderr; `LOG_LEVEL=debug` shows each cycle.

| Variable | Default | |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | |
| `REDIS_PASSWORD` | none | kept out of the URL |
| `SHARD_INDEX`, `SHARD_COUNT` | `0`, `1` | see below |
| `TFL_APP_KEY` | none | TfL providers only |
| `LOG_LEVEL` | `info` | |

## Sharding

To split one provider's ids between copies, run `SHARD_COUNT` copies with `SHARD_INDEX` from `0` to `SHARD_COUNT - 1`.
Each id goes to `sha256(id) % SHARD_COUNT` (`sharding.shard_of`), the same in every process, so exactly one copy
fetches it. Changing `SHARD_COUNT` moves most ids, which is harmless.

## Adding a provider

1. Add its stored model to `countdown_core/store/models.py` (subclass `Stored`, which carries `fetched_at`) and, if it's
   new, its key builder to `keys.py`.
2. Add `countdown_providers/<name>/main.py` with a class matching the `Provider` protocol (`provider.py`): `name` (the
   `v1:sub:<name>` set it reads), `ttl_s`, `interval_s`, `output_key(id)` and `async fetch(ids)`. `fetch` returns a
   model per id it fetched and leaves out (and logs) the rest rather than raising. If its ids say where a screen is,
   log failures with `log_format.error_reason(e)`, not `e`, and read postcodes' places with `LocationReader` (and
   `wakes_on = (keys.LOCATION_CHANGED,)`) rather than look them up. One that fetches the same thing for every screen
   sets `fixed_ids` instead (`bank_holidays/main.py`).
3. Give it a `main()` that calls `asyncio.run(serve(provider, Settings.from_env()))`, and add it to
   `[project.scripts]` in `pyproject.toml` as `countdown-provider-<name>`.
4. Add a `provider-<name>` service to `../server/deploy/docker-compose.yml` like the others.
5. Test it with its API mocked; `tests/test_provider_runner.py` covers the loop.

`weather/main.py` is the smallest complete example.

## The image

All providers are in one image, `ghcr.io/nvorkinn/countdown-providers:<tag>`, built by each release from its own wheels
and pinned to the release by the deploy. Its default command is the TfL provider; a service picks another with
`command`. To build it locally, from `countdown/`:

```sh
uv build --wheel --all-packages
docker build -f providers/Dockerfile -t countdown-providers:dev .
```

## In production

The providers are services in [`../server/deploy/docker-compose.yml`](../server/deploy/docker-compose.yml), so every
deploy starts and upgrades them, and `--wait` fails the deploy if one won't stay up. They need Redis on the host
(`127.0.0.1:6379`, over host networking), `REDIS_PASSWORD` and `TFL_APP_KEY` from the repo's secrets, and the broker
writing subscriptions (including `v1:sub:postcode`). A provider fetches as it starts, so its keys appear within
seconds. To check, on the host:

```sh
docker logs --tail 20 countdown-renderers-provider-floods-1
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 --scan --pattern "v1:notices:floods*"'
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 SMEMBERS v1:sub:postcode'
```

There should be one key per subscribed id (a UK postcode for floods). None means the provider isn't running (its log
says why) or the broker isn't writing the subscription set.
