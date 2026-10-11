# pi-dashboard

[![codecov](https://codecov.io/gh/nvorkinn/pi-dashboard/graph/badge.svg)](https://codecov.io/gh/nvorkinn/pi-dashboard)

A Raspberry Pi e-paper dashboard and the telemetry that goes with it, in one
repo:

- [`countdown/`](countdown/) -- the long-running Python app that drives the
  display. For a screen that only shows frames, its renderer runs as a Docker
  container next to the auth broker instead: see
  [`countdown/server/deploy/`](countdown/server/deploy/).
- [`fluent-bit/`](fluent-bit/) -- the [Fluent Bit](https://fluentbit.io) config `install.sh` sets up on
  a Pi: the systemd journal goes to VictoriaLogs (`logs.nikolaivorkinn.com`) and host metrics to
  `metrics.nikolaivorkinn.com`, both with a bearer token in the `Authorization` header. The token is the device's own secret, so
  Fluent Bit only runs once countdown has registered with the broker (see [Logs and metrics](#logs-and-metrics)).
- Root -- `install.sh`, which provisions a Pi with countdown and Fluent Bit, and `secrets/`, the
  encrypted config it hands them.

The countdown wheel and secrets bundle are released together: one tag builds
both, and `install.sh` installs exactly that tag.

Uses the [`gh` CLI](https://cli.github.com) (bootstrapped automatically if
missing) to resolve releases and download assets, since it handles
private-repo auth correctly on its own -- a hand-rolled `curl` approach
needs the asset API plus an `Accept` header, because a private repo's
`browser_download_url` doesn't work with a bearer token.

## Install

This repo is private, and fetching `install.sh` in the first place is also a
private-repo fetch, so you need a GitHub token with read access to it --
create one at
[github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens),
fine-grained, read-only, scoped to `pi-dashboard`. It has to be in your
shell *before* the very first `curl` that fetches this script.

Nothing here is ever fetched from `main` -- every fetch is pinned to a
specific tagged release. Check
[the releases page](https://github.com/nvorkinn/pi-dashboard/releases) for
the tag you want (there's no script running yet at the very first `curl` to
resolve "latest" for you, so it's the one place you look it up by hand) and
pass it explicitly, both in the URL and as the first argument. The script
installs exactly that release of both apps:

```sh
export GITHUB_TOKEN=github_pat_...
curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
    https://raw.githubusercontent.com/nvorkinn/pi-dashboard/v1.0.0/install.sh \
    | sudo -E env GITHUB_TOKEN="$GITHUB_TOKEN" bash -s -- \
        v1.0.0 <device name>
```

`<device name>` is required -- see [Device name](#device-name) below.

The first run also asks for the age private key (see below) via a hidden
prompt. Both the GitHub token and the age key are cached under
`/etc/pi-setup` (root-only) afterwards, so re-running the installer with a
newer tag to update doesn't ask again -- except `GITHUB_TOKEN` still needs to
be in your environment for that first `curl`, since it happens before this
script (and its cache) exists.

## Updates

`install.sh` also installs `check_update.sh` (as
`/usr/local/sbin/pi-dashboard-check-update`) and a systemd timer that runs
it hourly from 02:00 to 06:00, then every three hours (09:00, 12:00, ...,
00:00). If the GitHub release marked "latest" isn't what's installed, it
downloads that release's `install.sh` and runs it with the cached token and
device name.

The release workflow creates every release as a pre-release, so nothing
reaches the Pis until you promote it:

    gh release edit v1.2.0 --prerelease=false --latest

(or tick "Set as the latest release" on its release page). To roll back,
mark an older release as latest the same way.

    systemctl list-timers pi-dashboard-update.timer
    journalctl -u pi-dashboard-update
    sudo pi-dashboard-check-update   # check now

## Logs

`install.sh` keeps the journal on disk (`systemd/90-pi-dashboard-journal.conf`),
which Raspberry Pi OS otherwise keeps in RAM only, so the logs from before a
crash or reboot survive it. It keeps up to three days, in at most 50 MB.

    journalctl -u countdown -b -1   # the previous boot

## Releasing

Push a `v*` tag on a commit that's on `main`; `.github/workflows/release.yml`
builds everything and creates the GitHub release. The tag is the version of
the app -- it's stamped into the wheels at build time, so the
`version` in `countdown/pyproject.toml` is just a placeholder and doesn't need bumping.

The countdown CI workflow only runs when files under its folder change
(`.github/workflows/countdown-ci.yml`).

## Device name

Each Pi needs a name that identifies it in the logs and metrics -- pick something
that says whose it is, e.g. `sister-hat`. It's the second argument to
`install.sh`. The name is sanitized (trimmed, lowercased, anything outside
`[a-z0-9_-]` becomes `-`, same rules as `countdown_credentials/device_name.py`) and
written as `DEVICE_NAME=<name>` into `/etc/countdown/env` (loaded by countdown's systemd unit), replacing
any `DEVICE_NAME` already there; countdown copies it into Fluent Bit's env once registered. It's also saved to
`/etc/pi-setup/device-name`.

It isn't the `device_id` the auth broker gives a renderer when it registers.

countdown sends it to the broker as the `X-Device-Name` header and as the `host.name` on its OTLP metrics, and
Fluent Bit labels every log line with it as `host` and every metric as `device_name`.
Don't put `DEVICE_NAME` in the encrypted secrets -- they're shared by every
Pi, and `install.sh` overwrites it anyway.

Re-running with a different name changes it, and the logs and metrics then show up under the new
`host` / `device_name`, so `install.sh` warns when that happens. Re-run with the same name to keep it.

## Logs and metrics

On a standalone Pi, `install.sh` installs [Fluent Bit](https://fluentbit.io) (config: [`fluent-bit/fluent-bit.yaml`](fluent-bit/fluent-bit.yaml)),
which sends the systemd journal to VictoriaLogs (`logs.nikolaivorkinn.com`) and host metrics, plus countdown's own
OTLP gauges, to `metrics.nikolaivorkinn.com`. Both requests carry `Authorization: Bearer <token>`, and the token is
the device secret countdown registers with the broker (`.auth_broker_device`), so there's nothing to provision.

This is only for a standalone Pi (`countdown-standalone`). A renderer for a separate screen (`countdown-server`, in
Docker) does none of it: its host already runs a Fluent Bit, which the renderer just sends its OTLP metrics to at
`127.0.0.1:4318`, so there's no token and no systemd involved.

Every log line leaves with the same fields (shared with auth-broker's Docker logs), and VictoriaLogs streams are
`service,host`:

| Field | From |
|---|---|
| `service` | `_SYSTEMD_UNIT` without `.service` (`countdown`); the `SYSLOG_IDENTIFIER` for lines with no unit (`kernel`, `sudo`), else `unknown` |
| `host` | `DEVICE_NAME` |
| `level` | journald `PRIORITY`: 0-2 `critical`, 3 `error`, 4 `warn`, 5-6 `info`, 7 `debug` (`info` if missing) |
| `_msg` | `MESSAGE` |

`SYSLOG_IDENTIFIER`, `_PID` and `_COMM` are kept; the other journald fields are dropped. Changing these stream
fields starts new VictoriaLogs streams, so logs from before the change stay under the old
`device_name,_SYSTEMD_UNIT` streams.

Fluent Bit mustn't send before the broker has accepted that secret, so it's started by systemd only then:

1. `fluent-bit.service` is enabled, with a drop-in (`systemd/fluent-bit-env.conf`) that makes it conditional on
   `/run/countdown/fluent-bit-env`. Until that file exists it doesn't run, and it reads nothing from the journal.
2. When `register()` succeeds, countdown (via `countdown_credentials/log_shipping.py`, pointed at the file by
   `FLUENT_BIT_ENV_FILE` in `countdown.service`) writes `FLUENT_BIT_TOKEN` and `DEVICE_NAME` there. `/run` is RAM, so
   the secret is never on the SD card. The directory belongs to the install user (`RuntimeDirectory=` in the unit),
   so no root is needed, and Fluent Bit runs as that user too.
3. `fluent-bit-env.path` sees the write and restarts Fluent Bit. It starts with no saved journal position, so it
   also sends what was logged while the device was registering. The file is gone at every reboot and countdown writes
   it again, which starts Fluent Bit again; a changed secret (a new registration) restarts it.

## How secrets work

Each real secrets file (countdown's `.env`) is encrypted with [age](https://age-encryption.org)
and committed here as `secrets/<app>.env.age`. `install.sh` gets them from
the `secrets.zip` release asset (built by `.github/workflows/release.yml`)
via `gh release download`, not a direct file fetch. There's one keypair for
all of it:

- **Public key** -- safe to share, used to encrypt. Currently:
  `age1mrlql83ne3jewsuqzemmlrxsdscn9sqlksl4uqhy8da5krn75usq5u46ak`
- **Private key** -- decrypts everything. Lives only in
  `local-only/age-key.txt` on this machine (gitignored -- never committed)
  and cached at `/etc/pi-setup/age-key.txt` on each provisioned Pi. **Back
  this up somewhere outside git** (a password manager entry is enough) --
  if it's lost, every encrypted secret in this repo becomes unrecoverable
  and has to be re-created and re-encrypted from scratch.

If a given `secrets/<app>.env.age` doesn't exist yet, `install.sh` skips it
and that app falls back to its own defaults (countdown gets its config from
auth-broker anyway).

### Adding or updating a secret

Edit the real file locally (e.g. a scratch copy of countdown's `.env`), then:

```sh
age -e -r age1mrlql83ne3jewsuqzemmlrxsdscn9sqlksl4uqhy8da5krn75usq5u46ak \
    -o secrets/countdown.env.age /path/to/real/.env
```

Commit the resulting `.age` file. It's ciphertext -- safe in git even
though the repo is private anyway. Since `install.sh` only ever reads
secrets from the release tag it was fetched at, a new secret doesn't take
effect anywhere until you **cut a new release** (push a new tag) and re-run
the installer against it.

### Rotating the age key

Generate a new keypair (`age-keygen -o local-only/age-key.txt`), re-encrypt
every `secrets/*.age` file with the new public key, update the public key
in this README, and update `/etc/pi-setup/age-key.txt` on every Pi (or just
delete it there so the installer prompts for the new one on next run).

## What this does and doesn't protect against

Secrets are encrypted at rest in git and in transit, but once decrypted
onto a Pi they live as plain files (`chmod 600`, owned by the service
user) -- that's what `EnvironmentFile=`/`.env` loading requires. That's the
standard baseline for this kind of setup: it stops other unprivileged users
on the box and accidental exposure via git. It does **not** protect against
someone with root on the Pi, or someone with the physical SD card -- file
permissions don't apply once you're not going through the OS that set them.

The device's own identity, `.auth_broker_device`, is encrypted at rest (Fernet) with a per-device key that's
generated by `install.sh` into `/etc/pi-setup/credentials-key` (root's alone) and handed to the service by
systemd (`LoadCredential=`), so the file and its key are never readable by the same user. That stops a stray copy of
the file or of its volume; root on the Pi still has both. Fluent Bit needs the same secret as its bearer token in
plain text, so countdown writes it to `/run/countdown/fluent-bit-env`: RAM, gone at reboot, and never on the SD
card. It's still readable by root and the service's user while the Pi is running. A container's renderer has no
such copy. See [`countdown/server/deploy/`](countdown/server/deploy/) for its key.
