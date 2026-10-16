from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

    from countdown_core.utils.clock import ClockFormat


class Panel(ABC):
    @abstractmethod
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        msg = "Subclasses must implement the render method."
        raise NotImplementedError(msg)

    def fitted_height(self, max_height: int) -> int:
        """How tall the panel needs to be, at most `max_height`: 0 if it has nothing to draw.
        Most panels fill whatever they're given.
        """
        return max_height
