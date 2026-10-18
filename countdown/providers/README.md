# countdown-providers

One long-running process per API (TfL, Entur, weather, ...) that fetches what the screens want and writes it to a shared
Redis store, which the renderers read instead of each calling every API itself. Ten screens watching the
same bus stop then cost one TfL call, not ten.

**Status:** live. The TfL, Entur and weather providers (here) and the energy and music providers (auth-broker) fill
the store from the broker's subscriptions, and the renderers read only the store for those panels (see
[Enabling in production](#enabling-in-production)). There are no standalone Pis any more, so no renderer calls those
APIs itself.

## The model

```
auth-broker ──SADD (replace)──▶ v1:sub:tfl ──SMEMBERS──▶ provider-tfl ──SET … EX ttl──▶ v1:departures:<stop>
            ──PUBLISH───────▶ v1:sub:changed ──wakes──▶                                      │
                                                                                       renderers ◀──GET
```

- The **broker** writes, for each source, the set of ids some screen wants, replacing it atomically whenever a config
  is saved, then publishes on `v1:sub:changed` as a nudge.
- Each **provider** runs the same loop (`runner.ProviderRunner`): read its set, fetch what's in its shard, write each
  result with a TTL, then sleep until the next tick of its interval (epoch-aligned), or until the nudge or a shutdown.
- A **renderer** reads the keys. A stop with nothing due is still written (an empty list), so "no buses" isn't
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
| `v1:sub:developer` | set | broker | device ids with the developer panel on (with or without a GitHub token) |
| `v1:sub:postcode` | set | broker | the notice boards' postcodes in canonical form, e.g. `SW1A 1AA`, `0150` (Norwegian: four digits); keys by place use `keys.place()`, e.g. `SW1A1AA` |
| `v1:sub:changed` | pub/sub channel | broker | empty message after replacing subscriptions |
| `v1:departures:<stop id>` | string, TTL ~2 min | provider-tfl, provider-entur | `StopDepartures` JSON |
| `v1:weather:<key>` | string, TTL ~30 min | provider-weather | `StoredForecast` JSON (Open-Meteo's forecast; the renderer makes the pages) |
| `v1:energy:<device id>` | string, TTL ~1 h | auth-broker's glow-provider | `StoredEnergy` JSON (`store/energy.py`): Glowmarkt's own readings answers (`hourly`, `daily`, `monthly`) with the `offset` they were asked at; see [Energy](#energy) |
| `v1:spotify:<device id>` | string, TTL 3 min | auth-broker's `spotify-provider`, every minute | `StoredPlayer` JSON (`store/music.py`): each service's queue, as the broker's `/queue` route answers it |
| `v1:art:<sha1 of the image URL>` | string of raw bytes, TTL 5 min, renewed every minute while a `v1:spotify:` key shows it | auth-broker's `spotify-provider`, before the player key that shows it | the playing track's cover, the image file as its host served it (a JPEG of about 30 KB); one per image, shared by every screen showing it; see [Covers](#covers) |
| `v1:developer:<device id>` | string, TTL ~15 min | auth-broker's `developer-provider`, every 2 min, every 20 s while a release runs (a device whose token GitHub rate-limits is left alone until GitHub says) | `StoredDeveloper` JSON (`store/developer.py`): `github`, the GitHub summary (pull requests, builds, releases with their jobs), null until the owner gives a token, and `plans`, the AI plans; the broker has no route for the panel, so this key is the renderer's only source |
| `v1:pace:<source>:<id>` | integer string, TTL ~1 min | any provider that has bursty data (the developer provider, per device, while a release runs) | the seconds it wants the screen to refresh at; renderers take the fastest of those they watch, see the renderer's README, "The pace" |
| `v1:spotify-top:<device id>` | string, TTL 2 h | auth-broker's `spotify-provider`, every 30 min | `StoredTop` JSON (`store/music.py`): each service's top tracks and artists by time range |
| `v1:notices:met-office:<place>` | string, TTL ~30 min | provider-met-office, every 5 min | `StoredMetOfficeWarnings` JSON (`store/met_office.py`): the Met Office's warnings RSS for the region a UK postcode in `v1:sub:postcode` is in, its items as JSON; the renderer makes the notices. See [Weather warnings](#weather-warnings) |

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

### Covers

The playing track's cover is in the store too, as the image itself, so the renderer never calls Spotify's or Apple's
image hosts: `v1:art:<sha1 of its URL>` (`keys.album_art`), keyed by image rather than device, so every screen showing
the same album reads one copy. Of the album's `images`, the provider stores the smallest at least 120 px tall (the
Spotify area's height in every layout), else the tallest: Spotify's 300 px, and the one 300 px image the broker asks
Apple Music for. The renderer finds the key the same way (`store/music.py`'s `art_image`, with the URL exactly as the
service wrote it) and scales the image down to the style's size.

The value is the file's raw bytes, not JSON or base64 (which would take a third more of Redis's 128 MB), so it's read
with a client that doesn't decode values (`reader.binary_store()`; `reader.store()` decodes every value as text). The
provider writes it before the player key that shows it, and renews its TTL each minute it's still shown rather than
fetching it again, so it outlives that key; a cover nothing shows lapses within five minutes. A cover that isn't
there (its fetch failed, or Redis evicted it) or won't open draws the page without one, as for a local file: it never
fails the panel. `tests/fixtures/music_art.json` is the keys stored for `music_player.json`, each holding
`music_art.jpg` in the tests, in both repositories; auth-broker's README ("Music in the store") has the sizes.

### Energy

The energy panel's readings are written by auth-broker, not by a provider here: its `glow-provider` (the broker's image
with another command) signs in to Glowmarkt with the passwords the broker keeps encrypted, so they never leave it. It
can't import countdown-core, so what it stores is Glowmarkt's answers as they came, in a thin wrapper, and the renderer
reads them with the models of Glowmarkt's answers (`glow/models.py`):

```json
{"fetched_at": "2026-09-25T05:10:00+00:00", "device_id": "a1b2c3d4e5f6", "offset": "-60", "resource_id": "...",
 "hourly": {"data": [[1790229600, 0.21], ...], ...}, "daily": {...}, "monthly": {...}}
```

`hourly`, `daily` and `monthly` are Glowmarkt's `PT1H`, `P1D` and `P1M` readings, each asked for, as the renderer
once asked for them itself, from the start of its page's window (the hour 24 hours before, midnight 31 days before, 1st January) to
`fetched_at`, in the screen's time zone at its offset then. `offset` is Glowmarkt's parameter for that (minutes from
local time to UTC, so BST is `-60`): Glowmarkt labels each bucket with its local wall-clock start as if it were UTC, and
the renderer relabels them at that offset. It cuts each page to its window as of when it draws, so a value up to its
TTL old still gives the right 24 hours, 31 days and year, short only of the readings since `fetched_at`. The broker
fetches every 15 minutes, and keeps each value for an hour. `tests/fixtures/glow_energy.json` is one fetch, from
Glowmarkt's answers to the stored value, and auth-broker's tests read the same file: change both together.
`tests/fixtures/glow_energy_pages.json` is the pages the renderer drew from those answers when it still asked
Glowmarkt itself, which the stored value must still draw.

### Weather warnings

provider-met-office writes the notice board's Met Office warnings, by place: for each UK postcode in `v1:sub:postcode`
(a Norwegian one, four digits, is MET Norway's, and left alone), `v1:notices:met-office:<keys.place(postcode)>`, so a
renderer finds its warnings by its postcode alone. Each postcode is placed once with postcodes.io (a place doesn't
move) to the Met Office warning region it's in (`notices/met_office.py`'s `region_for`, the whole UK's feed for one it
can't place), and each region's feed is fetched once a cycle however many postcodes are in it, then written under each
of them. What's stored is the feed's items as they came, each element's text:

```json
{"fetched_at": "2026-09-25T11:55:00Z", "postcode": "SE17 3LL", "region": "se",
 "items": [{"title": "Amber warning of wind affecting London & South East England",
            "description": "...: Lambeth, Southwark valid from 0600 Fri 25 Sep to 2100 Fri 25 Sep",
            "link": "https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings#?...",
            "pub_date": "Thu, 24 Sep 2026 10:30:00 GMT"}]}
```

The renderer makes its notices from them each time it reads them (`warning_notices`): the colour, the hazard and the
day each ends, in the screen's language, as it once did from the feed itself. `tests/fixtures/met_office_warnings.xml`
is a feed, `met_office_warnings.json` the stored value made of it, and `met_office_notices.json` the notices the
renderer made from that feed when it fetched it itself, which the stored value must still make. Where the screens are
stays out of the provider's logs, as it does out of the renderer's: nothing logged names a postcode.

To deploy it: the provider must be running and its keys there before the renderers rely on them. It needs
auth-broker's postcode subscriptions (`v1:sub:postcode`, auth-broker #174) deployed first; check the set is there, on
the Redis host:

```sh
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 SMEMBERS v1:sub:postcode'
```

Then, once a release with `provider-met-office` is deployed (its first cycle runs as it starts), the keys:

```sh
docker exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 --scan --pattern "v1:notices:met-office*"'
```

There should be one per UK postcode in `v1:sub:postcode`; the provider's log says why one is missing (`docker logs
countdown-renderers-provider-met-office-1`). A renderer whose key isn't there logs "No Met Office warnings in the store
for the board's postcode", shows the rest of its notices, and keeps whatever Met Office warnings it last read (for up
to an hour); it reads again every 5 minutes.

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
shape for departures, each wrapping countdown-core's source (`TflSource`, `EnturSource`), which only the providers
call now. Entur, like Open-Meteo,
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

This repository's providers (TfL, weather, Entur, Met Office) are on: they're services in `../server/deploy/docker-compose.yml`,
so every deploy starts and upgrades them, and `--wait` fails the deploy if one won't stay up. `release.yml`'s
`deploy-renderers` job passes them the repo's secrets `REDIS_PASSWORD` (the same as auth-broker's `secrets/redis.env`)
and `TFL_APP_KEY`; without the password the deploy fails rather than start them unauthenticated. They need the broker
writing subscriptions (auth-broker) and Redis running on the host (`127.0.0.1:6379`, reached over host networking).
auth-broker's providers (energy, music, developer) are switched on in auth-broker, and deployed with the broker.

The renderers read the store: the orchestrator's `RENDERER_REDIS_URL` defaults to `redis://127.0.0.1:6379/0` in
the compose file (it gets `REDIS_PASSWORD` from the deploy). It passes them to each renderer as `REDIS_URL` and
`REDIS_PASSWORD`, and remakes the running ones (keeping their volumes), because a change to the store they read counts
as an upgrade, like a new image. The renderers read TfL's and Entur's departures (`v1:departures:<stop>`), the weather
(`v1:weather:<location>`), the energy readings (`v1:energy:<device id>`, from auth-broker's `glow-provider`) and the
Spotify area (`v1:spotify:<device>` and `v1:spotify-top:<device>`, and the covers, `v1:art:<sha1>`, from auth-broker's
`spotify-provider`) only from the store, and the developer panel (`v1:developer:<device id>`, from auth-broker's
`developer-provider`) too; of the notice board's sources, the Met Office's warnings (`v1:notices:met-office:<place>`)
come from the store, and the rest still straight from their APIs. So each provider must be running for its panels to
have anything to draw.

A renderer started without the store (`RENDERER_REDIS_URL` set to empty, so no `REDIS_URL`) can't build the clients
that read it: their areas say "Could not connect", the device reports them as in error, and the log names
`REDIS_URL` (`reader.StoreNotConfiguredError`). The rest of the screen still works. There's no going back to calling
the APIs directly: that code is gone.

A key a renderer wants that isn't in the store (its provider is down, or hasn't got to it yet), or a Redis that doesn't
answer, is a failed read, never fatal: that stop is left off, and a panel with nothing to read keeps its last
picture (or says "Could not connect" if it never had one).
