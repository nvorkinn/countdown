#!/usr/bin/env bash
# Run by release.yml's deploy-renderers job, on the runner, against the host's Docker daemon (DOCKER_HOST=ssh://...),
# so nothing is installed on the host. Pins the orchestrator and renderer image to the release, then updates the
# orchestrator; it brings assigned renderers to the new image. Run by hand it does the same to whatever daemon
# your docker talks to.
# Usage: deploy.sh <tag>
set -euo pipefail

export COUNTDOWN_SERVER_VERSION="$1"
cd "$(dirname "$0")"

docker compose pull
# Waits for the containers to be running, and fails the deploy if one isn't.
docker compose up -d --remove-orphans --wait --wait-timeout 120
docker compose ps

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
