"""Every module in the workspace imports, so a name one PR removes while another still imports it fails here first."""

import importlib
import pkgutil

import pytest

import countdown_core
import countdown_providers
import countdown_renderer

MODULES = sorted(
    module.name
    for package in (countdown_core, countdown_providers, countdown_renderer)
    for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")
)


def test_the_layouts_are_among_the_modules() -> None:
    """The walk finds the layout modules, so the test below covers what the render worker loads."""
    assert "countdown_renderer.rendering.layouts.hd_landscape_column" in MODULES


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    """`name` imports without error."""
    importlib.import_module(name)
