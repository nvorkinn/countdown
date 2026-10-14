"""Generates the broker client (config_server/broker_api/) from the countdown API contract.

    uv run --frozen --only-group dev python core/contract/generate.py        (from countdown/)

The contract is the latest release of https://github.com/nvorkinn/countdown-api, the same file auth-broker
generates its side from. Neither the file nor what comes out of it is committed: run this after cloning, and in
every build (CI and the release workflow do). A build therefore fails when the contract moves to something this
code no longer fits.

COUNTDOWN_API_SPEC points it at another file or URL instead, e.g. a countdown-api change that isn't released yet."""

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

LATEST = "https://github.com/nvorkinn/countdown-api/releases/latest/download/openapi.yaml"

CONTRACT_DIR = Path(__file__).resolve().parent
SPEC = CONTRACT_DIR / "openapi.yaml"
OUT = CONTRACT_DIR.parent / "src" / "countdown_core" / "config_server" / "broker_api"
# Only what a client needs: the Pydantic models and the `requests` client, no Flask.
PARTS = "clients"


def fetch(source: str) -> None:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=30) as response:
            SPEC.write_bytes(response.read())
    else:
        shutil.copyfile(source, SPEC)


def main() -> None:
    source = os.environ.get("COUNTDOWN_API_SPEC", LATEST)
    fetch(source)
    digest = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    version = next((line.split(":", 1)[1].strip() for line in SPEC.read_text().splitlines() if "version:" in line), "?")
    print(f"countdown API {version} (sha256 {digest}) from {source}")
    subprocess.run([sys.executable, "-m", "openapi_codegen", str(SPEC), "--out", str(OUT), "--only", PARTS], check=True)


if __name__ == "__main__":
    main()
