# countdown-renderer: the render pool (countdown-render-worker, the default, and countdown-render-ticker), which draws
# the frames the screens (the ESP32) fetch from the broker. Built from a release's wheels -- the build context is
# the repository root, after `uv build --wheel --all-packages` has filled dist/. See deploy/README.md.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/nvorkinn/countdown"
LABEL org.opencontainers.image.description="countdown render pool: the workers and the ticker"

# fribidi turns on Pillow's Raqm text layout, which the snapshot tests render with.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

# Third-party packages first, pinned and hashed from uv.lock (dev/build_dist.sh), in a layer of their own: the
# release's build cache keeps it until uv.lock changes, so a deploy doesn't pull them again with every release.
RUN --mount=type=bind,source=dist/requirements-renderer.txt,target=/tmp/requirements.txt \
    pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check --require-hashes \
    -r /tmp/requirements.txt

# Our packages only ever come from these wheels, never an index: anyone could publish a package there under one of
# their names. That includes countdown-api, which isn't on PyPI at all. --no-index also fails the build if the
# requirements above left a dependency out.
COPY dist/countdown_renderer-*.whl dist/countdown_core-*.whl dist/countdown_api-*.whl /tmp/wheels/
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check --no-index /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

# Downloaded on first use otherwise, again every time a container is recreated.
RUN python -c "from countdown_renderer.utils.utils import google_symbols; google_symbols()"
# Fail the build rather than render text differently from the snapshot tests.
RUN python -c "from PIL import features; assert features.check('raqm'), 'Pillow has no Raqm text layout'"

# It keeps nothing, so needs no volume.
RUN useradd --system --create-home countdown
USER countdown

ENV PYTHONUNBUFFERED=1
# A render worker; the ticker is the same image with `countdown-render-ticker` (renderer/deploy/docker-compose.yml).
ENTRYPOINT ["countdown-render-worker"]
