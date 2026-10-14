"""Takes over a screen's existing renderer, so the screen needn't be set up again. See "Running it in development"
in ../README.md.

    DOCKER_HOST=ssh://user@host uv run python dev/adopt_renderer.py <slug>

Copies the identity (.auth_broker_device) and the key that decrypts it out of the container the orchestrator
made, `renderer-<slug>`, and writes dev/.env and a PyCharm run configuration from its environment. Your process is then the same device the broker
already matched with the screen: nothing changes on the broker, and there's no claim token or orchestrator token.

Two renderers must never run as one device, so before you start yours, stop the orchestrator (it restarts a
stopped renderer within seconds) and then the renderer: `docker stop <orchestrator> renderer-<slug>`. When you're
done, start them again."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from dev_env import DEV_DIR, WORKING_DIRS, add_pycharm_arguments, write_env, write_pycharm_config

RENDERER_DIR = DEV_DIR / WORKING_DIRS[0]
IDENTITY_FILE = RENDERER_DIR / ".auth_broker_device"
# The container's XDG_CONFIG_HOME is /keys; here it's a directory of its own, so the key isn't mixed with
# the one in ~/.config. The path is relative to the renderer's working directory.
KEY_HOME = Path(".config")
KEY_PATH = Path("countdown") / "credentials-key"


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"docker {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slug", help="the renderer's slug, as picked on the broker's config page")
    parser.add_argument("--force", action="store_true", help="replace an identity already in dev/renderer")
    add_pycharm_arguments(parser)
    args = parser.parse_args()

    container = f"renderer-{args.slug}"
    if IDENTITY_FILE.exists() and not args.force:
        print(f"{IDENTITY_FILE} exists, so that's already a device. Use --force to replace it.")
        return 1

    info = json.loads(docker("inspect", container))[0]
    environment = dict(item.split("=", 1) for item in info["Config"]["Env"])
    if info["State"]["Running"]:
        print(f"Warning: {container} is running. Stop the orchestrator, then it, before you start yours.")

    key_file = RENDERER_DIR / KEY_HOME / KEY_PATH
    key_file.parent.mkdir(parents=True, exist_ok=True)
    docker("cp", f"{container}:/data/.auth_broker_device", str(IDENTITY_FILE))
    docker("cp", f"{container}:/keys/{KEY_PATH}", str(key_file))
    IDENTITY_FILE.chmod(0o600)
    key_file.chmod(0o600)

    env = write_env(environment["BROKER_URL"], environment["DEVICE_NAME"], "", XDG_CONFIG_HOME=str(KEY_HOME))
    write_pycharm_config(args, environment["DEVICE_NAME"], env)
    print(f"Adopted {container}: identity and key in {RENDERER_DIR}, environment in {DEV_DIR / '.env'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
