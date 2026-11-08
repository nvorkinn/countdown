#!/usr/bin/env bash
# Run by deploy-renderers.yml before deploy.sh: reads one environment's runtime secrets from Vaultwarden with the
# Bitwarden CLI and exports them to the rest of the job through $GITHUB_ENV, so deploy.sh and the compose files take
# them from the environment as ever. Nothing is written to disk but the CLI's own session state, which is removed on exit.
#
# The vault item is named "countdown/<environment>" (for example countdown/dev-qa) and holds a custom field for each
# secret: REDIS_PASSWORD (required) and TFL_APP_KEY (optional, empty if absent).
#
# Environment:
#   VW_SERVER_URL   The Vaultwarden server, e.g. https://vault.example.org
#   VW_ITEM         The item's name, e.g. countdown/dev-qa
#   BW_CLIENTID, BW_CLIENTSECRET   The API key of the account that may read the item (read by `bw login --apikey`)
#   BW_PASSWORD     That account's master password, to unlock the vault
#   GITHUB_ENV      Where to export to (set by GitHub Actions; for a trial run, point it at a temp file)
set -euo pipefail

: "${VW_SERVER_URL:?VW_SERVER_URL is not set}"
: "${VW_ITEM:?VW_ITEM is not set}"
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

item="$(bw get item "$VW_ITEM")"

# Prints the named custom field's value, or nothing when the item lacks it.
field() {
  jq -r --arg name "$1" '[.fields[]? | select(.name == $name) | .value // empty] | first // empty' <<<"$item"
}

export_secret() {
  local name="$1" required="$2" value
  value="$(field "$name")"
  if [ -z "$value" ]; then
    if [ "$required" = required ]; then
      echo "The vault item $VW_ITEM has no $name field (or it is empty)." >&2
      exit 1
    fi
    echo "$name is not set in $VW_ITEM; continuing without it."
    return
  fi
  # A newline would let the value inject further variables into $GITHUB_ENV.
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "The $name field of $VW_ITEM contains a line break, which is not supported." >&2
    exit 1
  fi
  # Mask before it can reach a log, then hand it to the following steps.
  echo "::add-mask::$value"
  printf '%s=%s\n' "$name" "$value" >>"$GITHUB_ENV"
  echo "Fetched $name from $VW_ITEM."
}

export_secret REDIS_PASSWORD required
export_secret TFL_APP_KEY optional
