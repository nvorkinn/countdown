from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.rendering.screen import Canvas
from countdown_core.utils.clock import ClockFormat

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Frame:
    """A finished picture, the size of the canvas it was drawn for."""

    image: Image.Image
    # The one part of it that changes often enough to be worth a partial refresh (the
    # arrivals), or None if every change should repaint the whole screen.
    volatile: Box | None = None


class Layout(ABC):
    """One way of arranging the panels on a canvas. Layouts are stateless: everything they
    know is in class attributes, so the registry can hand the same instance out every cycle."""

    # What the config's layout choice names. Layouts sharing an id are its variants, one per
    # combination of the APIs a device can show.
    layout_id: ClassVar[str]
    canvases: ClassVar[frozenset[Canvas]]
    # This variant fits when every API in `requires` is available and none in `excludes` is.
    requires: ClassVar[frozenset[ClientClasses]] = frozenset()
    excludes: ClassVar[frozenset[ClientClasses]] = frozenset()
    # The TfL client fetches this many stops per update and pages through the rest.
    stops_shown: ClassVar[int] = 2

    def fits(self, available: frozenset[ClientClasses]) -> bool:
        return self.requires <= available and not self.excludes & available

    @abstractmethod
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        """The whole screen, from a panel for every API (a MessagePanel where one has nothing)."""

    @staticmethod
    def _place(
        img: Image.Image,
        panel: Panel,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        clock_format: ClockFormat = "24h",
    ) -> Image.Image:
        """Renders `panel` and pastes it at (x, y). Returns what was rendered, since some
        panels (the arrivals) choose their own size."""
        rendered = panel.render(width, height, clock_format=clock_format)
        img.paste(rendered, (x, y), rendered)
        return rendered
