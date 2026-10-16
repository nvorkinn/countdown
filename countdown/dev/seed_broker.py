r"""Seed a *local* auth-broker's database with a screen that is already set up, paired and named.

Its renderer is assigned and claimed too, so nobody goes through WiFi, pairing, naming or the config page. See
"Running it in development" in ../README.md.

    uv run python dev/seed_broker.py ../../auth-broker/data/broker.db --owner <user id> --slug nikolai-dev \
        --weather London --postcode "SW1A 1AA" --stops 490000077E

It adds a device (paired to --owner, the id the login proxy sends as X-Authentik-Uid) with its config and a
`provisioned` renderer assignment that has no claim token left, then writes both processes' identities: the
renderer's to dev/renderer (with the device_id, so it never claims) and the screen's secret to dev/screen,
each encrypted with a key of its own beside it, plus dev/.env and a PyCharm run configuration for the renderer
(.idea/runConfigurations/, with the environment set up). Nothing else has to be set up: run that configuration,
and start the screen with dev/screen as its working directory.

It writes the broker's tables directly, so it only runs against the schema revision it was written for. Run it
against a local broker only: there's no orchestrator to race, and the broker's secrets are throwaway.
"""

import argparse
import hashlib
import json
import re
import secrets
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet
from dev_env import DEV_DIR, WORKING_DIRS, add_pycharm_arguments, write_env, write_pycharm_config

# The auth-broker migration this was written for (broker/migrations/versions/0009_device_size.py).
EXPECTED_REVISION = "0009"
SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}")
IDENTITY_NAME = ".auth_broker_device"
# Each process's key is under its own XDG_CONFIG_HOME (see countdown_credentials.credentials_key); the
# relative path is the one in dev/.env, which both processes resolve from their working directory.
KEY_HOME = Path(".config")
KEY_PATH = Path("countdown") / "credentials-key"
# Tables with a row per device, in the order they can be deleted.
DEVICE_TABLES = ("frames", "pairing_codes", "spotify_tokens", "glowmarkt_credentials", "device_config")


def hash_secret(secret: str) -> str:
    """auth_broker's auth.hash_secret."""
    return hashlib.sha256(secret.encode()).hexdigest()


def pairing_token(display_secret_hash: str) -> str:
    """auth_broker's services.pairing.token_for."""
    return hashlib.sha256(f"pairing:{display_secret_hash}".encode()).hexdigest()


def timestamp() -> str:
    """How the broker's UTCDateTime is stored: naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def write_identity(role: str, secret: str, device_id: str | None) -> None:
    """Write the file a registrar keeps (see countdown_credentials.registration.Registrar._save).

    It's encrypted with a new key for this process. A renderer with its device_id is already registered: it makes
    no claim.
    """
    directory = DEV_DIR / role
    key_file = directory / KEY_HOME / KEY_PATH
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    data = {"device_secret": secret} | ({"device_id": device_id} if device_id else {})
    identity = directory / IDENTITY_NAME
    identity.write_bytes(Fernet(key).encrypt(json.dumps(data).encode()))
    identity.chmod(0o600)


def forget(db: sqlite3.Connection, slug: str) -> None:
    """Delete the screen with this slug and everything the broker keeps for it, if there is one."""
    row = db.execute("SELECT device_id FROM renderer_assignments WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return
    for table in DEVICE_TABLES:
        db.execute(f"DELETE FROM {table} WHERE device_id = ?", row)  # noqa: S608 -- fixed table names
    db.execute("DELETE FROM renderer_assignments WHERE slug = ?", (slug,))
    db.execute("DELETE FROM devices WHERE device_id = ?", row)


def main() -> int:
    """Seed the screen the command line describes; the exit status is 1 if the schema or the slug is wrong for it."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("db", type=Path, help="the broker's SQLite file (data/broker.db in its checkout)")
    parser.add_argument("--owner", required=True, help="the user the screen is paired with (X-Authentik-Uid)")
    parser.add_argument("--slug", required=True, help="the screen's (and renderer's) name, e.g. nikolai-dev")
    parser.add_argument("--broker-url", default="http://127.0.0.1:5000")
    parser.add_argument("--weather", default="", help="weather location")
    parser.add_argument("--postcode", default="", help="the notice board's postcode")
    parser.add_argument("--stops", nargs="*", default=[], help="TfL NaPTAN stop ids, in order")
    parser.add_argument("--interval", type=int, default=20, help="seconds between updates")
    parser.add_argument("--orientation", choices=("horizontal", "vertical"), default="horizontal")
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--replace", action="store_true", help="first remove the screen already made with this slug")
    add_pycharm_arguments(parser)
    args = parser.parse_args()
    if not SLUG_PATTERN.fullmatch(args.slug):
        parser.error(f"{args.slug!r} isn't a valid slug ({SLUG_PATTERN.pattern})")
    if not args.db.is_file():
        parser.error(f"{args.db} doesn't exist: start the broker once, which creates and migrates it")

    db = sqlite3.connect(args.db)
    try:
        revision = db.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision is None or revision[0] != EXPECTED_REVISION:
            print(f"The broker's schema is at {revision and revision[0]}, not {EXPECTED_REVISION}: update this script.")
            return 1
        if args.replace:
            forget(db, args.slug)
        elif db.execute("SELECT 1 FROM renderer_assignments WHERE slug = ?", (args.slug,)).fetchone():
            print(f"{args.slug!r} is taken. Use --replace to make it again.")
            return 1

        renderer_secret, display_secret = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        device_id = secrets.token_hex(6)
        now = timestamp()
        display_hash = hash_secret(display_secret)
        db.execute(
            "INSERT INTO devices (device_id, renderer_secret_hash, display_secret_hash, pairing_token, owner, "
            "created_at, paired_at, device_name, last_seen_at, display_last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                device_id,
                hash_secret(renderer_secret),
                display_hash,
                pairing_token(display_hash),
                args.owner,
                now,
                now,
                args.slug,
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO device_config (device_id, interval, weather_location, postcode, spotify_enabled, "
            "tfl_stop_ids, orientation, width, height) VALUES (?,?,?,?,0,?,?,?,?)",
            (
                device_id,
                args.interval,
                args.weather,
                args.postcode,
                json.dumps(args.stops),
                args.orientation,
                args.width,
                args.height,
            ),
        )
        db.execute(
            "INSERT INTO renderer_assignments (id, device_id, slug, claim_token, status, detail, created_at) "
            "VALUES (?,?,?,NULL,'provisioned','',?)",
            (secrets.token_hex(6), device_id, args.slug, now),
        )
        db.commit()
    finally:
        db.close()

    for role in WORKING_DIRS:
        (DEV_DIR / role).mkdir(exist_ok=True)
    write_identity("renderer", renderer_secret, device_id)
    write_identity("screen", display_secret, None)
    env = write_env(args.broker_url, args.slug, "", XDG_CONFIG_HOME=str(KEY_HOME))
    write_pycharm_config(args, args.slug, env)
    print(f"Seeded {args.slug!r} (device {device_id}), paired to {args.owner}.")
    print(f"Identities and keys: {DEV_DIR / 'renderer'}, {DEV_DIR / 'screen'}; environment: {DEV_DIR / '.env'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
