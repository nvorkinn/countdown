# countdown-server: the render pool (countdown-render-worker, the default, and countdown-render-ticker), which draws
# the frames the screens (the ESP32) fetch from the broker. Built from a release's wheels -- the build context is
# countdown/, after `uv build --wheel --all-packages` has filled countdown/dist/. See deploy/README.md.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/nvorkinn/pi-dashboard"
LABEL org.opencontainers.image.description="countdown render pool: the workers and the ticker"

# fribidi turns on Pillow's Raqm text layout, which the snapshot tests render with.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

# Our packages only ever come from these wheels, never an index: anyone could publish a
# package there under one of their names. That includes countdown-api, which isn't on PyPI at all.
COPY dist/countdown_server-*.whl dist/countdown_core-*.whl dist/countdown_api-*.whl /tmp/wheels/
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

# Downloaded on first use otherwise, again every time a container is recreated.
RUN python -c "from countdown_core.utils.utils import google_symbols; google_symbols()"
# Fail the build rather than render text differently from the snapshot tests.
RUN python -c "from PIL import features; assert features.check('raqm'), 'Pillow has no Raqm text layout'"

# It keeps nothing, so needs no volume.
RUN useradd --system --create-home countdown
USER countdown

ENV PYTHONUNBUFFERED=1
# A render worker; the ticker is the same image with `countdown-render-ticker` (server/deploy/docker-compose.yml).
ENTRYPOINT ["countdown-render-worker"]
