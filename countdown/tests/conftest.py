from datetime import UTC, datetime, timedelta
from pathlib import Path

import fakeredis
import pytest
from PIL import Image, ImageFont
from test_utils import highlight_diff, images_equal

from countdown_core.core import rotation
from countdown_core.store import reader
from countdown_core.utils import utils


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test in an empty temp directory, with DEVICE_NAME cleared."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVICE_NAME", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def store(monkeypatch):
    """Return the store every test's renderer reads, as if it had been given REDIS_URL: an empty fake Redis.

    Every renderer reads one, and the departures, weather, energy and music clients can't be built without it. A test
    puts what a provider would have written there; one about a renderer without a store sets reader.store to give None.

    reader.binary_store (for the covers, which are bytes) reads the same fake Redis, so an image put here is read there.
    """
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    binary = fakeredis.FakeRedis(server=server)
    monkeypatch.setattr(reader, "store", lambda: client)
    monkeypatch.setattr(reader, "binary_store", lambda: binary)
    return client


# The minute every test starts on, for the panels that turn a page a minute (core.rotation): 2026-01-15 00:00 UTC is
# a whole multiple of 2520 minutes since the epoch, so every page count from 1 to 10 is on its first page then.
ROTATION_START = datetime(2026, 1, 15, tzinfo=UTC)


class RotationClock:
    """The clock the rotating panels turn their pages by, stopped at ROTATION_START until a test moves it."""

    def __init__(self) -> None:
        self.at = ROTATION_START

    def now(self) -> datetime:
        return self.at

    def advance(self, minutes: int = 1) -> None:
        """Move on `minutes` minutes: a page each, for every rotating panel."""
        self.at += timedelta(minutes=minutes)

    def at_minute(self, minute: int) -> None:
        """Stop at `minute` minutes after ROTATION_START, so a panel of n pages is on page minute % n."""
        self.at = ROTATION_START + timedelta(minutes=minute)


@pytest.fixture(autouse=True)
def rotation_clock(monkeypatch) -> RotationClock:
    """Freeze the rotating panels' clock (core.rotation.now) on a minute where every panel shows its first page.

    Only the pages' clock: everything else still reads the real time. A test that turns pages moves it (advance).
    """
    clock = RotationClock()
    monkeypatch.setattr(rotation, "now", clock.now)
    return clock


@pytest.fixture
def symbols_font(monkeypatch):
    """Stand in for Material Symbols, the one font the renderer downloads, with one it ships with (like no_network).

    For the tests of what's drawn when, not how: they then draw the same offline as with the font downloaded.
    """
    monkeypatch.setattr(
        utils, "_cached_font", lambda url, filename: ImageFont.truetype(utils.FONTS_DIR / "Ubuntu-Regular.ttf")
    )
    utils.google_symbols.cache_clear()
    yield
    utils.google_symbols.cache_clear()  # don't leave the stand-in cached for other tests


# --- Visual regression snapshots -------------------------------------------------
# Addressed by __file__, not cwd, since isolated_cwd chdirs.
SNAPSHOT_DIR = Path(__file__).parent / "images"
FAILURE_DIR = SNAPSHOT_DIR / "_failures"

# Snapshots compare exactly: they're drawn, and compared, in the pinned Linux image (dev/snapshots.sh), where the
# render is byte-identical on amd64 and arm64. A Mac's or a CI runner's own rendering differs, so don't run them bare.


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "snapshot: compares a render with a golden in tests/images/; run in the pinned Linux image (dev/snapshots.sh)",
    )


def pytest_collection_modifyitems(items):
    """Mark every test that takes the `snapshot` fixture: `-m snapshot` picks them out, `-m 'not snapshot'` the rest."""
    for item in items:
        if "snapshot" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.snapshot)


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

    def assert_matches(self, name: str, image: Image.Image) -> None:
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
            # Saved like a mismatch's, so CI's failure artifact holds the new render too.
            FAILURE_DIR.mkdir(parents=True, exist_ok=True)
            actual.save(failure_actual)
            pytest.fail(
                f"No snapshot for '{name}' yet at {golden_path}; the render is at {failure_actual}. Run "
                "`dev/snapshots.sh update`, review the generated image, then commit it."
            )

        golden = Image.open(golden_path).convert("RGB")
        if images_equal(golden, actual):
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
            "If this change is intentional, run `dev/snapshots.sh update`, review "
            "the new image, and commit it."
        )


@pytest.fixture
def snapshot(request) -> Snapshot:
    return Snapshot(update=request.config.getoption("--update-snapshots"))
