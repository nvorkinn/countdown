#!/usr/bin/env bash
# Run by deploy-renderers.yml before deploy.sh: reads one environment's runtime secrets from Vaultwarden with the
# Bitwarden CLI and exports them to the rest of the job through $GITHUB_ENV, so deploy.sh and the compose files take
# them from the environment as ever. Nothing is written to disk but the CLI's own session state, which is removed on exit.
#
# Each secret is its own vault item, named by the bare secret (REDIS_PASSWORD) and kept in the collection VW_COLLECTION
# (for example countdown/dev-qa), so the same name can be a different item per environment and an item that several
# projects share can sit in each one's collection. The value is as the login's password or, failing that, as the item's notes. REDIS_PASSWORD is required;
# TFL_APP_KEY is optional (empty if there is no such item).
#
# Environment:
#   VW_SERVER_URL     The Vaultwarden server, e.g. https://vault.example.org
#   VW_COLLECTION     The collection holding the items, e.g. countdown/dev-qa (the vault's "Default collection/" prefix is optional)
#   BW_CLIENTID, BW_CLIENTSECRET   The API key of the account that may read the items (read by `bw login --apikey`)
#   BW_PASSWORD       That account's master password, to unlock the vault
#   GITHUB_ENV        Where to export to (set by GitHub Actions; for a trial run, point it at a temp file)
set -euo pipefail

: "${VW_SERVER_URL:?VW_SERVER_URL is not set}"
: "${VW_COLLECTION:?VW_COLLECTION is not set}"
: "${BW_CLIENTID:?BW_CLIENTID is not set}"
: "${BW_CLIENTSECRET:?BW_CLIENTSECRET is not set}"
: "${BW_PASSWORD:?BW_PASSWORD is not set}"
: "${GITHUB_ENV:?GITHUB_ENV is not set}"

# The CLI keeps its state (and the API key's session) under this directory; keep it out of the home directory and
# delete it afterwards.
BITWARDENCLI_APPDATA_DIR="$(mktemp -d)"
export BITWARDENCLI_APPDATA_DIR
cleanup() {
  bw logout >/dev/null 2>&1 || true
  rm -rf "$BITWARDENCLI_APPDATA_DIR"
}
trap cleanup EXIT

bw config server "$VW_SERVER_URL" >/dev/null
bw login --apikey >/dev/null
BW_SESSION="$(bw unlock --passwordenv BW_PASSWORD --raw)"
export BW_SESSION
bw sync >/dev/null
collection_id="$(bw list collections | jq -er --arg c "$VW_COLLECTION" '[.[] | select(.name == $c or (.name | endswith("/" + $c)))] | if length == 1 then .[0].id else error("no single collection named \($c)") end')"

export_secret() {
  local name="$1" required="$2" item value error="$BITWARDENCLI_APPDATA_DIR/error"
  # Exactly one item of this name must be in the collection. The CLI's stderr is kept apart so that a warning can't
  # end up in the JSON on success.
  if ! item="$(bw list items --collectionid "$collection_id" --search "$name" 2>"$error" |
    jq -ce --arg n "$name" '[.[] | select(.name == $n)] | if length == 1 then .[0] else error("\(length) items named \($n)") end' 2>>"$error")"; then
    item="$(cat "$error")"
    if [ "$required" = required ]; then
      echo "Could not read the vault item $name in $VW_COLLECTION: $item" >&2
      exit 1
    fi
    echo "No readable vault item $VW_COLLECTION/$name ($item); continuing without $name."
    return
  fi
  value="$(jq -r '.login.password // .notes // empty' <<<"$item")"
  if [ -z "$value" ]; then
    if [ "$required" = required ]; then
      echo "The vault item $VW_COLLECTION/$name has no password or notes to take the value from." >&2
      exit 1
    fi
    echo "$VW_COLLECTION/$name is empty; continuing without $name."
    return
  fi
  # A newline would let the value inject further variables into $GITHUB_ENV.
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "The value of $VW_COLLECTION/$name contains a line break, which is not supported." >&2
    exit 1
  fi
  # Mask before it can reach a log, then hand it to the following steps.
  echo "::add-mask::$value"
  printf '%s=%s\n' "$name" "$value" >>"$GITHUB_ENV"
  echo "Fetched $name from $VW_COLLECTION/$name."
}

export_secret REDIS_PASSWORD required
export_secret TFL_APP_KEY optional
