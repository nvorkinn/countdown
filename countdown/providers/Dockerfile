# countdown-providers: every provider (one per API) in one image; each Compose service picks its provider by
# command, e.g. `countdown-provider-weather`. Built from a release's wheels -- the build context is countdown/, after
# `uv build --wheel --all-packages` has filled countdown/dist/. See README.md.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/nvorkinn/pi-dashboard"
LABEL org.opencontainers.image.description="countdown providers: fetch each API's data into the shared Redis store"

# Our packages only ever come from these wheels, never an index: anyone could publish a package there under one of
# their names. countdown-core (and with it -credentials and -api) is here for its API clients and the store's models.
COPY dist/countdown_providers-*.whl dist/countdown_core-*.whl dist/countdown_credentials-*.whl dist/countdown_api-*.whl /tmp/wheels/
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

# Stateless: everything it keeps is in Redis, so no volume and no home of its own.
RUN useradd --system --no-create-home countdown
USER countdown

ENV PYTHONUNBUFFERED=1
# CMD rather than ENTRYPOINT, so a service's `command` swaps in another provider.
CMD ["countdown-provider-tfl"]
