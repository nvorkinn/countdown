# countdown-orchestrator

Starts a renderer for every screen that has been set up, so nobody adds a service to
`server/deploy/docker-compose.yml` by hand. It runs on the renderers' host, next to Docker, and polls the auth
broker. The flow only ever goes one way:

1. A screen powers on, registers with the broker (`role=display`) and the user finishes WiFi and account setup.
2. On the config page the user picks a **slug** (`[a-z0-9][a-z0-9_-]{1,31}`), which names the device in logs and
   metrics. The broker stores an *assignment* for the screen: `pending`, with a one-time claim token.
3. The orchestrator sees it, starts a `countdown-server` container with `DEVICE_NAME=<slug>` and
   `CLAIM_TOKEN=<token>`, and reports `provisioned`.
4. The renderer claims its screen with the token (`POST /api/renderers/claim`), which matches it at once.

The unit is an *assignment* (a screen's renderer), not a container, so one renderer serving several screens
later only changes where `reconcile()` places an assignment.

## Running it

`countdown-orchestrator`, configured by environment:

| Variable | |
|---|---|
| `BROKER_URL` | required; also handed to the renderers |
| `ORCHESTRATOR_TOKEN` | required; its own bearer credential, not a device secret |
| `RENDERER_IMAGE` | required; e.g. `ghcr.io/nvorkinn/countdown-server:<release>` |
| `MAX_RENDERERS` | optional capacity, of renderers *running*; assignments beyond it stay `pending`; `0` means unlimited |
| `RENDERER_MEM_LIMIT` | default `256m` |
| `POLL_INTERVAL_S` | default 10 |
| `RENDERER_REDIS_URL`, `REDIS_PASSWORD` | the store the renderers read (handed to them as `REDIS_URL`), which the orchestrator writes the render pool's screens to |
| `POOL_SLUGS` | optional: the slugs, separated by commas, of the screens the render pool draws instead of their own renderers (see [The render pool](#the-render-pool)); empty: none |

It runs as a container beside the renderers: the `orchestrator` service in
`../server/deploy/docker-compose.yml`, deployed with every release (`ORCHESTRATOR_TOKEN` is a repo secret, read by
the `deploy-renderers` job). It needs the host's Docker socket, which is root on the host: run it only there, and
never give the socket to the broker. Its renderers aren't compose services, so the deploy never touches them.
Containers it makes are labelled `countdown.managed=1`, so it only lists and removes its own. A renderer with no
assignment is logged and left alone, never deleted, so a broker bug or an empty reply can't wipe identities.

Each pass is idempotent (a reboot or crash just runs it again):

- **Wanted** renderers that are missing or stopped are made or started.
- **Upgrades:** a renderer made from an older `RENDERER_IMAGE` (the label `countdown.image`) is replaced by one
  from the current image, keeping its volumes, so it's the same device. A release's deploy therefore upgrades them.
- **`suspended`** assignments have their renderer stopped, never deleted. When the screen is back the broker lists
  the assignment as `pending` again and the same container (and identity) is started.
- **`deleting`** assignments are removed **with their volumes** (identity and key).
- Each outcome is reported to the broker.

## The render pool

Step 6 of [Stateless renderers](../docs/stateless-renderers.md#migration): the render pool's workers draw the screens
listed in `v1:render:screens` (device id to slug), and until the broker writes that hash itself, the orchestrator does,
for the screens in `POOL_SLUGS` (`pool.py`):

- **Handing over**, on every pass: a screen in `POOL_SLUGS` that's wanted on (pending, provisioned or failed) has its
  renderer **stopped**, keeping the container and its volumes, and is then written into the hash. It's reported
  `provisioned`. Every pass, so a Redis restart (which empties the hash) is undone within one.
- **Handing back**: a screen in the hash that isn't to be pooled any more (taken out of `POOL_SLUGS`, suspended, being
  deleted, or gone) is taken out of it, and its renderer is started again (on the current image, with its volumes)
  once its lease `v1:render:lease:<device id>` has gone: a render of it in flight finishes first, and no new one can
  start, since the workers' claim checks the hash.
- It needs the screen's `device_id` from the broker (auth-broker #202). Without it, the screen is logged once and left
  on its own renderer.
- With `POOL_SLUGS` empty nothing changes. With Redis unreachable, the screens in `POOL_SLUGS` are left as they are.

`POOL_SLUGS` comes from the `deploy-renderers` job's repository variable of the same name (Settings > Secrets and
variables > Actions > Variables). **To move a screen to the pool**, add its slug there (e.g. `nvorkinn-test`) and
deploy (re-run the latest release's `deploy-renderers` job, or release). **To roll back**, take it out (or clear the
variable) and deploy again: the orchestrator removes it from the hash, waits for its lease to lapse, and starts its
renderer again.

## The broker's side (to build in auth-broker)

Authenticated with `Authorization: Bearer <ORCHESTRATOR_TOKEN>`.

**`GET /api/orchestrator/assignments`** returns every assignment that is not `deleted`:

```json
{"assignments": [{"id": "a1", "slug": "lisa", "claim_token": "…", "status": "pending", "device_id": "a1b2c3d4e5f6"}]}
```

`device_id` is the screen's (null once the device is forgotten); a broker before auth-broker #202 leaves it out.
`status` is `pending`, `provisioned`, `suspended`, `failed` (retried on every pass) or `deleting`.

**The broker owns the policy** for suspending: when the assignment's screen hasn't been seen for 24 hours it
sets `suspended`, and when the screen is seen again it sets `pending`. (The orchestrator doesn't know when
screens were last seen.)

**`POST /api/orchestrator/assignments/{id}/status`** with `{"status": "provisioned|failed|deleted", "detail": ""}`.
`deleted` removes the assignment for good.

**Creating one** (config page, signed in): `POST /api/displays/{id}/renderer {"slug": "lisa"}` validates the slug
(`[a-z0-9][a-z0-9_-]{1,31}`, and unique **across the whole broker**) and answers at once. The slug is the
device's name in logs and metrics, and it names the container and its volumes, so it can't be changed once set
(the assignment's own `id` is what the broker keys on).

**The claim token** is random, single-use, and kept only until it's used: the orchestrator needs it back to
start the container, so it can't be stored hashed. The broker clears it when the renderer claims it.

**Claiming with a token**: `POST /api/renderers/claim` with `{"secret": …, "claim_token": …}` matches the renderer
to the assignment's screen. It answers 201 `{"device_id": …}` on the first claim, and 200 `{"device_id": …}` for a
repeat with the same secret (a restart may call it again; the token is then ignored). 400 means a bad secret
(at least 16 characters) or a missing `claim_token`, 403 an unknown or spent token, 409 a secret already
registered as a screen. The route isn't throttled. The renderer logs a rejection and retries slowly. After a
claim the renderer's own secret is its identity (kept in its volume). A renderer can't register without a claim
token, and there's no pool to wait in.
