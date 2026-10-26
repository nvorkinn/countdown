"""The provider processes draw nothing, so their mains must not load Pillow or the panel base class."""

import subprocess
import sys

import pytest

PROVIDERS = [
    "bank_holidays",
    "entur",
    "entur_situations",
    "floods",
    "locations",
    "met_office",
    "metalerts",
    "tfl",
    "tfl_notices",
    "weather",
]

HEAVY = ("PIL", "countdown_renderer")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_provider_main_imports_without_pillow_or_the_panel_base(provider: str) -> None:
    """Importing the main in a fresh interpreter leaves PIL and abstract_client out of sys.modules."""
    code = f"import sys, countdown_providers.{provider}.main; print(sorted(set(sys.modules) & set({HEAVY!r})))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603
    assert result.stdout.strip() == "[]"
