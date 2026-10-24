#!/usr/bin/env bash
# Run by release.yml's deploy-renderers job, on the runner, against the host's Docker daemon (DOCKER_HOST=ssh://...),
# so nothing is installed on the host. Pins the providers' and the render pool's images to the release, brings them
# up, removes any per-screen renderer containers and volumes left on the host, and drops older image tags. Run by
# hand it does the same to whatever daemon your docker talks to.
# Usage: deploy.sh <tag>
set -euo pipefail

export COUNTDOWN_RENDERER_VERSION="$1"
cd "$(dirname "$0")"

docker compose pull
# Waits for the containers to be running, and fails the deploy if one isn't. --pull never, as the pull above just did
# (each service's pull_policy: always would otherwise check every image again).
docker compose up -d --pull never --remove-orphans --wait --wait-timeout 120
docker compose ps

# Leftover per-screen renderers: every renderer-<slug> container (labelled countdown.managed=1), then its volumes,
# renderer-<slug>-data and renderer-<slug>-key. Nothing starts them, as the render pool draws every screen. Only
# volumes no container uses (dangling) go. Once they're gone, this does nothing.
renderers="$(docker ps -aq --filter label=countdown.managed=1)"
if [ -n "$renderers" ]; then
  # shellcheck disable=SC2086 # one argument per container id
  names="$(docker inspect -f '{{.Name}}' $renderers | tr -d / | tr '\n' ' ')"
  echo "Removing the per-screen renderers: $names"
  # shellcheck disable=SC2086
  docker rm -f $renderers
fi
for volume in $(docker volume ls -q --filter dangling=true --filter name=renderer-); do
  if [[ "$volume" =~ ^renderer-.+-(data|key)$ ]]; then
    echo "Deleting a per-screen renderer's volume: $volume"
    docker volume rm "$volume"
  fi
done

# --remove-orphans took any removed services' containers; remove volumes no longer declared too. Only this
# project's volumes (compose's label). If none are declared, skip cleanup to preserve data rather than risk
# deleting every volume.
project="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
declared="$(docker compose config --volumes)"
if [ -n "$declared" ]; then
  for volume in $(docker volume ls -q --filter "label=com.docker.compose.project=$project"); do
    if ! grep -qxF "${volume#"${project}_"}" <<< "$declared"; then
      echo "Deleting the volume of a removed service: $volume"
      docker volume rm "$volume"
    fi
  done
else
  echo "No named volumes declared; skipping volume cleanup to preserve data" >&2
fi

# Every release pulls new tags, so drop the older ones of what was just deployed: keep the running tag and the
# newest other one (to roll back to), remove the rest. `docker image rm` refuses an image a container still uses,
# and only these repositories are touched, so a deploy running at once (this or countdown-server's, on the same host)
# never loses an image it just pulled. The same cleanup as countdown-server's scripts/deploy.sh.
IMAGES_KEPT=2
for image in $(docker compose config --images | sort -u); do
  repo="${image%:*}"
  docker images "$repo" --format '{{.Repository}}:{{.Tag}}' | { grep -vxF "$image" || true; } | tail -n +"$IMAGES_KEPT" |
    while read -r old; do
      docker image rm "$old" >/dev/null 2>&1 && echo "Removed old image $old" || true
    done
done
# The render pool's image from before it was renamed countdown-renderer. ghcr.io/nvorkinn/countdown-server is now
# the broker's (countdown-server's) on the same host, so only the tags with the render pool's entrypoint go.
for old in $(docker images ghcr.io/nvorkinn/countdown-server --format '{{.Repository}}:{{.Tag}}'); do
  if [ "$(docker image inspect -f '{{json .Config.Entrypoint}}' "$old" 2>/dev/null)" = '["countdown-render-worker"]' ]; then
    docker image rm "$old" >/dev/null 2>&1 && echo "Removed old image $old" || true
  fi
done
# Layers left untagged by the above or by re-pulled tags.
docker image prune -f >/dev/null
