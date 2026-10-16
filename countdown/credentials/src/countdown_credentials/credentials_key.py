"""The key that keeps the device credentials file encrypted at rest. Under systemd it can be handed to the service
as a credential (LoadCredential=), so it never lives next to the file; a container gets it as a Docker secret. Anywhere else (a dev machine) one is made in the user's config directory, so the file is
never written unencrypted.
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet

KEY_NAME = "credentials-key"
# Where Docker mounts a Compose secret.
DOCKER_SECRETS_DIR = Path("/run/secrets")


def _owner_only(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


def _own_key() -> bytes:
    """This user's own key, made on first use."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    path = Path(config_home) / "countdown" / KEY_NAME
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb", opener=_owner_only) as file:
                file.write(Fernet.generate_key())
        except FileExistsError:
            pass  # made by another process in the meantime
    return path.read_bytes()


def credentials_key() -> Fernet:
    """The cipher for the credentials file."""
    directories = [Path(d) for d in (os.environ.get("CREDENTIALS_DIRECTORY"),) if d]
    for directory in (*directories, DOCKER_SECRETS_DIR):
        path = directory / KEY_NAME
        if path.is_file():
            return Fernet(path.read_bytes().strip())
    return Fernet(_own_key().strip())
