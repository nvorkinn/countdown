# countdown-server: the renderer for a screen that only shows frames (countdown-client or the
# ESP32). Built from a release's wheels -- the build context is countdown/, after
# `uv build --wheel --all-packages` has filled countdown/dist/. See deploy/README.md.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/nvorkinn/pi-dashboard"
LABEL org.opencontainers.image.description="countdown renderer for a separate screen"

# fribidi turns on Pillow's Raqm text layout, which the Pis and the snapshot tests render with.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

# Our packages only ever come from these wheels, never an index: anyone could publish a
# package there under one of their names. That includes countdown-api, which isn't on PyPI at all.
COPY dist/countdown_server-*.whl dist/countdown_core-*.whl dist/countdown_credentials-*.whl dist/countdown_api-*.whl /tmp/wheels/
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

# Downloaded on first use otherwise, again every time a container is recreated.
RUN python -c "from countdown_core.utils.utils import google_symbols; google_symbols()"
# Fail the build rather than render text differently from the Pis.
RUN python -c "from PIL import features; assert features.check('raqm'), 'Pillow has no Raqm text layout'"

RUN useradd --system --create-home countdown && mkdir /data /keys && chown countdown:countdown /data /keys
USER countdown
# The renderer's identity (.auth_broker_device) is written to its working directory.
WORKDIR /data
VOLUME /data
# Its key is made on first start, in a volume of its own so it isn't in the same copy as the identity it encrypts.
# A mounted Docker secret (/run/secrets/credentials-key) is used instead if there is one.
ENV XDG_CONFIG_HOME=/keys
VOLUME /keys

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["countdown-server"]
