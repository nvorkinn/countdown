from pathlib import Path

import fakeredis
import pytest
from PIL import Image
from test_utils import highlight_diff, images_equal

from countdown_core.store import reader


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test in an empty temp directory, with the app's env vars cleared.

    Device-local files like .auth_broker_device land there.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BROKER_URL", raising=False)
    # Where a key is made when the device wasn't given one.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("DEVICE_NAME", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def store(monkeypatch):
    """Return the store every test's renderer reads, as if it had been given REDIS_URL: an empty fake Redis.

    Every renderer reads one, and the departures, weather, energy and music clients can't be built without it. A test
    puts what a provider would have written there; one about a renderer without a store sets reader.store to give None.
    """
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: client)
    return client


# --- Visual regression snapshots -------------------------------------------------
# Addressed by __file__, not cwd, since isolated_cwd chdirs.
SNAPSHOT_DIR = Path(__file__).parent / "images"
FAILURE_DIR = SNAPSHOT_DIR / "_failures"

# Pillow's rasterization differs by a few anti-aliased pixels between macOS and Linux
# even with identical fonts and code; a real regression moves far more than this.
DEFAULT_SNAPSHOT_THRESHOLD = 0.005


def pytest_addoption(parser):
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Write tests/images/ golden files from the current render instead of "
        "comparing against them. Review the resulting diff before committing.",
    )


class Snapshot:
    def __init__(self, *, update: bool) -> None:
        self.update = update

    def assert_matches(self, name: str, image: Image.Image, threshold: float = DEFAULT_SNAPSHOT_THRESHOLD) -> None:
        golden_path = SNAPSHOT_DIR / f"{name}.png"
        failure_actual = FAILURE_DIR / f"{name}.actual.png"
        failure_diff = FAILURE_DIR / f"{name}.diff.png"
        actual = image.convert("RGB")

        if self.update:
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            actual.save(golden_path)
            failure_actual.unlink(missing_ok=True)
            failure_diff.unlink(missing_ok=True)
            return

        if not golden_path.exists():
            pytest.fail(
                f"No snapshot for '{name}' yet at {golden_path}. Run "
                "`pytest --update-snapshots`, review the generated image, then commit it."
            )

        golden = Image.open(golden_path).convert("RGB")
        if images_equal(golden, actual, threshold=threshold):
            failure_actual.unlink(missing_ok=True)
            failure_diff.unlink(missing_ok=True)
            return

        FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        actual.save(failure_actual)
        diff_note = ""
        if golden.size == actual.size:
            highlight_diff(golden, actual).save(failure_diff)
            diff_note = f" and diff (changed pixels in red) at {failure_diff}"

        pytest.fail(
            f"Rendered output for '{name}' no longer matches tests/images/{name}.png.\n"
            f"Actual render saved to {failure_actual}{diff_note}.\n"
            "If this change is intentional, run `pytest --update-snapshots`, review "
            "the new image, and commit it."
        )


@pytest.fixture
def snapshot(request) -> Snapshot:
    return Snapshot(update=request.config.getoption("--update-snapshots"))
