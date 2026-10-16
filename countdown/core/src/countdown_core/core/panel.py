from abc import ABC, abstractmethod

from PIL import Image


class Panel(ABC):
    @abstractmethod
    def render(self, image_width: int, image_height: int) -> Image.Image:
        raise NotImplementedError("Subclasses must implement the render method.")

    def fitted_height(self, max_height: int) -> int:
        """How tall the panel needs to be, at most `max_height`: 0 if it has nothing to draw.
        Most panels fill whatever they're given."""
        return max_height
