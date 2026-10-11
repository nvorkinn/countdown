# countdown-orchestrator: starts a renderer container for every screen the broker says needs one. Built
# from a release's wheels -- the build context is countdown/, after `uv build --wheel --all-packages` has
# filled countdown/dist/. It runs on the renderers' host and needs the host's Docker socket. See README.md.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/nvorkinn/pi-dashboard"
LABEL org.opencontainers.image.description="countdown orchestrator: runs a renderer for each screen"

# Our package only ever comes from this wheel, never an index (its dependencies, docker and requests, do).
COPY dist/countdown_orchestrator-*.whl /tmp/wheels/
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

# Root, like anything with the Docker socket: its group's id differs from host to host, and the socket is
# root on the host whoever in the container holds it.
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["countdown-orchestrator"]
