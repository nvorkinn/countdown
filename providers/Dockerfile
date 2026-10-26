# countdown-providers: every provider (one per API) in one image; each Compose service picks its provider by
# command, e.g. `countdown-provider-weather`. Built from a release's wheels -- the build context is the repository
# root, after `uv build --wheel --all-packages` has filled dist/. See README.md.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/nvorkinn/countdown"
LABEL org.opencontainers.image.description="countdown providers: fetch each API's data into the shared Redis store"

# Third-party packages first, pinned and hashed from uv.lock (dev/build_dist.sh), in a layer of their own: the
# release's build cache keeps it until uv.lock changes, so a deploy doesn't pull them again with every release.
RUN --mount=type=bind,source=dist/requirements-providers.txt,target=/tmp/requirements.txt \
    pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check --require-hashes \
    -r /tmp/requirements.txt

# Our packages only ever come from these wheels, never an index: anyone could publish a package there under one of
# their names. --no-index also fails the build if the requirements above left a dependency out. countdown-shared (and
# with it countdown-api) is here for its API clients and the store's models.
COPY dist/countdown_providers-*.whl dist/countdown_shared-*.whl dist/countdown_api-*.whl /tmp/wheels/
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check --no-index /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

# Stateless: everything it keeps is in Redis, so no volume and no home of its own.
RUN useradd --system --no-create-home countdown
USER countdown

ENV PYTHONUNBUFFERED=1
# CMD rather than ENTRYPOINT, so a service's `command` swaps in another provider.
CMD ["countdown-provider-tfl"]
