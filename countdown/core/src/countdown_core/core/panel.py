from abc import ABC, abstractmethod

from PIL import Image

from countdown_core.utils.clock import ClockFormat


class Panel(ABC):
    @abstractmethod
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        raise NotImplementedError("Subclasses must implement the render method.")
