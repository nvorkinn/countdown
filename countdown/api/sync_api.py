# /// script
# requires-python = ">=3.12"
# ///
"""Copies the latest nvorkinn/countdown-api source into src/.

    uv run sync_api.py [<latest.json URL or path>]

The version and the GitHub release come from https://nvorkinn.github.io/countdown-api/latest.json. The release's
wheel carries the generated source (countdown_api/), which replaces src/countdown_api/ here, after the wheel's
checksum is verified against latest.json. The version is recorded in VERSION.
"""

import hashlib
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

LATEST = "https://nvorkinn.github.io/countdown-api/latest.json"
HERE = Path(__file__).parent


def read(location: str) -> bytes:
    if "://" in location:
        with urllib.request.urlopen(location) as response:
            return response.read()
    return Path(location).read_bytes()


def main(location: str = LATEST) -> None:
    latest = json.loads(read(location))
    version, release = latest["version"], latest["release"]
    wheel = f"countdown_api-{version}-py3-none-any.whl"
    digest = latest["files"][wheel]
    data = read(f"{release.replace('/releases/tag/', '/releases/download/')}/{wheel}")
    if hashlib.sha256(data).hexdigest() != digest:
        sys.exit(f"{wheel} doesn't match the checksum in latest.json")

    target = HERE / "src" / "countdown_api"
    shutil.rmtree(target, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.startswith("countdown_api/") and not name.endswith("/"):
                destination = HERE / "src" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
    (HERE / "VERSION").write_text(f"{version}\n")
    print(f"countdown-api {version} ({release}) copied to {target}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
