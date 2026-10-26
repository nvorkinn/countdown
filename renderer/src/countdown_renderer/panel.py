"""The base class of everything drawn in an area of the screen."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from PIL import Image

    from countdown_renderer.utils.clock import ClockFormat


class Panel(ABC):
    """One API's area of the screen, drawn at whatever size the layout gives it."""

    # Whether it draws itself bigger inside scaled_draw.drawing_at(), for a screen with more pixels: a panel that
    # doesn't has its pixels repeated instead.
    scales: ClassVar[bool] = False

    @abstractmethod
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        """Draw the panel, `image_width` by `image_height` pixels."""
        msg = "Subclasses must implement the render method."
        raise NotImplementedError(msg)

    def fitted_height(self, max_height: int) -> int:
        """How tall the panel needs to be, at most `max_height`: 0 if it has nothing to draw.

        Most panels fill whatever they're given.
        """
        return max_height
