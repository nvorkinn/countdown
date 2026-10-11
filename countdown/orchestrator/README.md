# countdown-orchestrator

Starts a renderer for every screen that has been set up, so nobody adds a service to
`server/deploy/docker-compose.yml` by hand. It runs on the renderers' host, next to Docker, and polls the auth
broker. The flow only ever goes one way:

1. A screen powers on, registers with the broker (`role=display`) and the user finishes WiFi and account setup.
2. On the config page the user picks a **slug** (`[a-z0-9][a-z0-9_-]{1,31}`), which names the device in logs and
   metrics. The broker stores an *assignment* for the screen: `pending`, with a one-time claim token.
3. The orchestrator sees it, starts a `countdown-server` container with `DEVICE_NAME=<slug>` and
   `CLAIM_TOKEN=<token>`, and reports `provisioned`.
4. The renderer registers with the token, which matches it to its screen at once (no waiting in the pool).

The unit is an *assignment* (a screen's renderer), not a container, so one renderer serving several screens
later only changes where `reconcile()` places an assignment.

## Running it

`countdown-orchestrator`, configured by environment:

| Variable | |
|---|---|
| `BROKER_URL` | required; also handed to the renderers |
| `ORCHESTRATOR_TOKEN` | required; its own bearer credential, not a device secret |
| `RENDERER_IMAGE` | required; e.g. `ghcr.io/nvorkinn/countdown-server:<release>` |
| `MAX_RENDERERS` | optional capacity; assignments beyond it stay `pending` |
| `RENDERER_MEM_LIMIT` | default `256m` |
| `POLL_INTERVAL_S` | default 10 |

It needs the Docker socket, which is root on the host: run it only there, and never give the socket to the broker.
Containers it makes are labelled `countdown.managed=1`, so it only lists and removes its own. A renderer with no
assignment is logged and left alone, never deleted, so a broker bug or an empty reply can't wipe identities.

Each pass is idempotent (a reboot or crash just runs it again): wanted renderers that are missing or stopped are
made or started, `deleting` ones are removed **with their volumes** (identity and key), and each outcome is reported.

## The broker's side (to build in auth-broker)

Authenticated with `Authorization: Bearer <ORCHESTRATOR_TOKEN>`.

**`GET /api/orchestrator/assignments`** returns every assignment that is not `deleted`:

```json
{"assignments": [{"id": "a1", "slug": "lisa", "claim_token": "…", "status": "pending"}]}
```

`status` is `pending`, `provisioned`, `failed` (retried on every pass) or `deleting`.

**`POST /api/orchestrator/assignments/{id}/status`** with `{"status": "provisioned|failed|deleted", "detail": ""}`.
`deleted` removes the assignment for good.

**Creating one** (config page, signed in): `POST /api/displays/{id}/renderer {"slug": "lisa"}` validates the slug
(format, and unique; scope to be decided), stores the claim token **hashed**, and answers at once.

**Registering with a token**: `POST /api/devices/register` with `{"role": "renderer", "secret": …, "claim_token": …}`
matches the renderer to the assignment's screen and answers 201 `{"device_id": …}`, as a standalone renderer
gets. The token is single-use; after that the renderer's own secret is its identity (the renderer keeps it in its
volume and doesn't register again). Reject an unknown or spent token with 4xx; the renderer logs it and retries
slowly.
