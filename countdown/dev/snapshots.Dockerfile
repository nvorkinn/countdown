# The image the snapshot tests render in, locally and in CI, so the goldens in tests/images/ are drawn on one
# platform. Built from countdown/ as the context (dev/snapshots.sh does). It holds the OS, Python, the locked
# third-party packages and the one downloaded font; the repo is mounted at run time, never copied in.

# Pinned by digest: the manifest list, so linux/amd64 and linux/arm64 both resolve from it.
ARG PYTHON_IMAGE=python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
FROM ${PYTHON_IMAGE}

# fribidi turns on Pillow's Raqm text layout, which the snapshots are drawn with.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# The locked packages, in a layer that only a change to a pyproject or uv.lock rebuilds. Our own packages aren't
# installed: the mounted repo's src/ directories are put on PYTHONPATH instead (dev/snapshots.sh).
WORKDIR /work/countdown
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/
COPY core/pyproject.toml core/
COPY server/pyproject.toml server/
COPY providers/pyproject.toml providers/
RUN uv sync --frozen --no-install-workspace

# Material Symbols, the one font the renderer downloads (utils.google_symbols), from a pinned commit and checked
# against its hash. Copied to the mounted repo's fonts/ at run time, where the renderer looks for it.
ARG SYMBOLS_COMMIT=49d4db35df873165d6bd6ba09b063c7dafbac2f4
ARG SYMBOLS_SHA256=77ff1c20896afc6f1abf7d57fc408234dfd021ab050b63e0442e71d6c9f0d6b8
RUN python - <<'PY'
import hashlib, os, pathlib, urllib.request
name = "MaterialSymbolsOutlined[FILL,GRAD,opsz,wght].ttf"
url = (
    "https://github.com/google/material-design-icons/raw/"
    f"{os.environ['SYMBOLS_COMMIT']}/variablefont/MaterialSymbolsOutlined%5BFILL,GRAD,opsz,wght%5D.ttf"
)
data = urllib.request.urlopen(url, timeout=60).read()
digest = hashlib.sha256(data).hexdigest()
print("Material Symbols sha256:", digest)
expected = os.environ.get("SYMBOLS_SHA256", "")
if not expected:
    raise SystemExit("SYMBOLS_SHA256 is empty: the font can't be verified")
if digest != expected:
    raise SystemExit(f"Material Symbols changed: {digest} != {expected}")
out = pathlib.Path("/opt/fonts")
out.mkdir()
(out / name).write_bytes(data)
PY
