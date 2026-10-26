#!/bin/sh
# Runs the snapshot tests (the ones using the `snapshot` fixture) in the pinned Linux image they're drawn in, so the
# goldens in tests/images/ are the same on a Mac and in CI.
#
#   dev/snapshots.sh test [pytest args]     compare every snapshot with its golden, exactly
#   dev/snapshots.sh update [pytest args]   redraw the goldens; review `git diff --stat tests/images` before committing
#   dev/snapshots.sh build                  only build the image
#
# SNAPSHOT_PLATFORM sets the image's platform (default: the host's, linux/arm64 on an Apple-silicon Mac).
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
image="${SNAPSHOT_IMAGE:-countdown-snapshots}"
mode="${1:-test}"
[ $# -gt 0 ] && shift

build() {
  docker build ${SNAPSHOT_PLATFORM:+--platform "$SNAPSHOT_PLATFORM"} \
    -f "$here/snapshots.Dockerfile" -t "$image" "$repo"
}

case "$mode" in
  build) build; exit ;;
  test) flags="" ;;
  update) flags="--update-snapshots" ;;
  *) echo "usage: $0 test|update|build [pytest args]" >&2; exit 2 ;;
esac

# CI builds the image itself (and caches it); SNAPSHOT_SKIP_BUILD=1 then reuses it.
[ -n "${SNAPSHOT_SKIP_BUILD:-}" ] || build

src="/work"
# As the host's user, so what the tests write into the mounted repo (tests/images, _failures) isn't owned by root.
# The renderer finds Material Symbols in fonts/ (git-ignored), so the image's copy goes there first.
exec docker run --rm ${SNAPSHOT_PLATFORM:+--platform "$SNAPSHOT_PLATFORM"} \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e PYTHONPATH="$src/api/src:$src/shared/src:$src/renderer/src:$src/providers/src" \
  -v "$repo:/work" -w "$src" "$image" \
  sh -c 'cp -n /opt/fonts/* renderer/src/countdown_renderer/fonts/ && exec pytest -p no:cacheprovider -n auto -m snapshot "$@"' \
  sh $flags "$@"
