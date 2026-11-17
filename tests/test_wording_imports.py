"""The notices' wording is the providers' too, which draw nothing, so it must not load Pillow or the renderer."""

import subprocess
import sys

import pytest

MODULES = [
    "core.clock",
    "notices.wording.calendar",
    "notices.wording.developer",
    "notices.wording.entur",
    "notices.wording.floods",
    "notices.wording.met_office",
    "notices.wording.norway",
    "notices.wording.notice",
    "notices.wording.parcels",
    "notices.wording.tfl",
]

HEAVY = ("PIL", "countdown_renderer")


@pytest.mark.parametrize("module", MODULES)
def test_a_wording_module_imports_without_pillow_or_the_renderer(module: str) -> None:
    """Importing the module in a fresh interpreter leaves PIL and countdown_renderer out of sys.modules."""
    code = f"import sys, countdown_shared.{module}; print(sorted(m for m in sys.modules if m.startswith({HEAVY!r})))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603
    assert result.stdout.strip() == "[]"
