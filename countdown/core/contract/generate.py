"""Generates countdown_core/config_server/wire.py from broker-openapi.json: the models for what the broker sends.

    uv run python core/contract/generate.py

broker-openapi.json is a copy of auth-broker's openapi.json (https://github.com/nvorkinn/auth-broker). To pick
up a change to the broker's API, copy the new one over it and run this; tests/test_broker_contract.py fails
while either is out of date. wire.py is generated -- change the broker's contract, not this file.
"""

import subprocess
import sys
from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parent
SPEC = CONTRACT_DIR / "broker-openapi.json"
OUTPUT = CONTRACT_DIR.parent / "src" / "countdown_core" / "config_server" / "wire.py"

HEADER = (
    '"""GENERATED from core/contract/broker-openapi.json by core/contract/generate.py; do not edit.\n'
    "\n"
    "The broker's wire format. AppConfig (models.py) is what the app uses; the tests check it accepts\n"
    'everything in here."""\n'
)


def generate(output: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(SPEC),
            "--input-file-type",
            "openapi",
            "--output",
            str(output),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.14",
            "--use-union-operator",
            "--use-standard-collections",
            "--disable-timestamp",
            "--use-double-quotes",
            "--custom-file-header",
            HEADER,
            # Not ruff: it formats by the nearest config, so the same input would come out differently
            # written to a temp file (as the test does) than to its place here.
            "--formatters",
            "builtin",
        ],
        check=True,
    )


if __name__ == "__main__":
    generate(OUTPUT)
