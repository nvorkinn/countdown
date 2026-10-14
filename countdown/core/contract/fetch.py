"""Fetches the broker client (config_server/broker_api/) from the countdown API's latest release.

    python3 core/contract/fetch.py        (from countdown/)

https://github.com/nvorkinn/countdown-api builds the code from its OpenAPI contract with openapi-codegen and
publishes it with each release, which auth-broker takes the other half of. This downloads the release's
countdown_api.tar.gz, checks it against the release's SHA256SUMS, and unpacks the models and the `requests` client
(not the Flask views) into config_server/broker_api/. It also saves the contract itself as core/contract/openapi.yaml,
which the contract tests read. Neither is committed: run this after cloning, and in every build (CI and the release
workflow do), so a build fails when the contract moves to something this code no longer fits. It needs nothing but
Python.

COUNTDOWN_API_BASE points it at another URL or directory holding the same three files (openapi.yaml,
countdown_api.tar.gz, SHA256SUMS), e.g. a countdown-api change that isn't released yet."""

import hashlib
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

LATEST = "https://github.com/nvorkinn/countdown-api/releases/latest/download"
ARCHIVE = "countdown_api.tar.gz"
CONTRACT = "openapi.yaml"
CHECKSUMS = "SHA256SUMS"

CONTRACT_DIR = Path(__file__).resolve().parent
OUT = CONTRACT_DIR.parent / "src" / "countdown_core" / "config_server" / "broker_api"
# What a client needs of the package: not blueprints/, the broker's Flask views.
WANTED = ("countdown_api/__init__.py", "countdown_api/models.py", "countdown_api/clients/")


def fetch(base: str, name: str, destination: Path) -> None:
    if base.startswith(("http://", "https://")):
        with urllib.request.urlopen(f"{base.rstrip('/')}/{name}", timeout=60) as response:
            destination.write_bytes(response.read())
    else:
        shutil.copyfile(Path(base) / name, destination)


def checked(directory: Path, name: str, sums: str) -> Path:
    """The file in `directory`, once it matches its line in SHA256SUMS."""
    expected = {line.split()[1].lstrip("*"): line.split()[0] for line in sums.splitlines() if line.strip()}
    path = directory / name
    if name not in expected:
        sys.exit(f"{name} isn't in the release's {CHECKSUMS}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
        sys.exit(f"{name} doesn't match the release's {CHECKSUMS}")
    return path


def main() -> None:
    base = os.environ.get("COUNTDOWN_API_BASE", LATEST)
    download = CONTRACT_DIR / ".download"
    download.mkdir(exist_ok=True)
    for name in (CHECKSUMS, CONTRACT, ARCHIVE):
        fetch(base, name, download / name)
    sums = (download / CHECKSUMS).read_text()
    contract = checked(download, CONTRACT, sums)
    archive = checked(download, ARCHIVE, sums)

    version = next(line.split(":", 1)[1].strip() for line in contract.read_text().splitlines() if "version:" in line)
    print(f"countdown API {version} from {base}")
    shutil.copyfile(contract, CONTRACT_DIR / CONTRACT)

    if OUT.exists():
        shutil.rmtree(OUT)
    with tarfile.open(archive) as tar:
        members = [m for m in tar.getmembers() if m.name.startswith(WANTED) and "__pycache__" not in m.name]
        for member in members:
            member.name = member.name.removeprefix("countdown_api/")
        tar.extractall(OUT, members=members, filter="data")
    shutil.rmtree(download)
    print(f"Unpacked {sum(1 for m in members if m.isfile())} files into {OUT}")


if __name__ == "__main__":
    main()
