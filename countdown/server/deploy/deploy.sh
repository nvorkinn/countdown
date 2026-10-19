#!/usr/bin/env bash
# Run by release.yml's deploy-renderers job, on the runner, against the host's Docker daemon (DOCKER_HOST=ssh://...),
# so nothing is installed on the host. Pins the providers' and the render pool's images to the release and brings
# them up, then removes what's left of the per-screen renderers. Run by hand it does the same to whatever daemon your
# docker talks to.
# Usage: deploy.sh <tag>
set -euo pipefail

export COUNTDOWN_SERVER_VERSION="$1"
cd "$(dirname "$0")"

docker compose pull
# Waits for the containers to be running, and fails the deploy if one isn't.
docker compose up -d --remove-orphans --wait --wait-timeout 120
docker compose ps

# The per-screen renderers the orchestrator made (docs/stateless-renderers.md, step 9): the render pool draws every
# screen, and the orchestrator is gone (--remove-orphans took its container), so nothing starts them again. Each
# renderer-<slug> container (labelled countdown.managed=1) goes, and then its two volumes, renderer-<slug>-data and
# renderer-<slug>-key: the identity it claimed its screen with, which nothing uses any more. Only volumes no container
# uses (dangling), so nothing in use is touched. Once they're gone, this does nothing.
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
# deleting every identity.
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
