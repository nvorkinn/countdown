"""What a layout is: a way of arranging the panels on a canvas, and the frame it draws."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from countdown_core.core.api_registry import ClientClasses
from countdown_core.utils.utils import GAP

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.core.panel import Panel
    from countdown_core.rendering.screen import Canvas
    from countdown_core.utils.clock import ClockFormat

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Frame:
    """A finished picture, the size of the canvas it was drawn for."""

    image: Image.Image


class Layout(ABC):
    """One way of arranging the panels on a canvas.

    Layouts are stateless: everything they know is in class attributes, so the registry can hand the same instance
    out every render.
    """

    # What the config's layout choice names. Layouts sharing an id are its variants, one per
    # combination of the APIs a device can show.
    layout_id: ClassVar[str]
    canvases: ClassVar[frozenset[Canvas]]
    # This variant fits when every API in `requires` is available and none in `excludes` is.
    requires: ClassVar[frozenset[ClientClasses]] = frozenset()
    excludes: ClassVar[frozenset[ClientClasses]] = frozenset()
    # The departures client reads this many stops per poll and pages through the rest (ApiRegistry.show_stops).
    stops_shown: ClassVar[int] = 2

    def fits(self, available: frozenset[ClientClasses]) -> bool:
        """Whether this variant suits a device that can show the `available` APIs."""
        return self.requires <= available and not self.excludes & available

    @abstractmethod
    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        """Draw the whole screen, from a panel for every API (a MessagePanel where one has nothing)."""

    @staticmethod
    def _fit_notices(panels: dict[str, Panel], room: int) -> tuple[int, int]:
        """How tall to draw the notices, and how much of `room` that leaves spare for the arrivals.

        The notices are only as tall as their rows, up to `room` (0 leaves them out: none to show, or no notice
        board). The spare room includes the gap above them.
        """
        panel = panels.get(ClientClasses.NOTICE_BOARD.api_name)
        height = 0 if panel is None else panel.fitted_height(room)
        return height, room - height if height else room + GAP

    @staticmethod
    def _spotify_area(panels: dict[str, Panel], height: int) -> list[tuple[Panel, int]]:
        """What's drawn in Spotify's `height`-tall area, top down, and how tall each is.

        That's Spotify, with the developer panel as a strip above it when rendering.place_developer_panel left it under
        its own name.
        """
        area = [panels[ClientClasses.SPOTIFY.api_name]]
        developer = panels.get(ClientClasses.DEVELOPER.api_name)
        if developer is not None:
            area.insert(0, developer)
        return [(panel, panel.fitted_height(height)) for panel in area]

    @staticmethod
    def _place(
        img: Image.Image,
        panel: Panel,
        at: tuple[int, int],
        size: tuple[int, int],
        *,
        clock_format: ClockFormat = "24h",
    ) -> Image.Image:
        """Render `panel` `size` (width, height) big and paste it with its top left `at` (x, y).

        Returns what was rendered, since some panels (the arrivals) choose their own size.
        """
        rendered = panel.render(*size, clock_format=clock_format)
        img.paste(rendered, at, rendered)
        return rendered
