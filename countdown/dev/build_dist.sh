#!/bin/sh
# Fills dist/ with what the images install: every workspace member's wheel, and for each image its third-party
# requirements, pinned and hashed from uv.lock. The images install those in a layer of their own, which stays the same
# from release to release until uv.lock changes, so a deploy only pulls the layers holding our own packages. Run from
# countdown/, before `docker build`; release.yml and tests.yml do the same.
set -e
uv build --wheel --all-packages
for image in renderer providers; do
  uv export --frozen --no-emit-workspace --no-dev --no-header --quiet --package "countdown-$image" \
    -o "dist/requirements-$image.txt"
done
