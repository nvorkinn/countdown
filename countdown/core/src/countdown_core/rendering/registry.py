import logging
from typing import TYPE_CHECKING

from countdown_core.rendering.screen import DEFAULT_CANVAS, Canvas

if TYPE_CHECKING:
    from collections.abc import Iterable

    from countdown_core.core.api_registry import ClientClasses
    from countdown_core.rendering.layout import Layout

logger = logging.getLogger(__name__)


class LayoutRegistry:
    """Every layout, by the canvas it draws on. select() is a lookup with nothing to remember
    (beyond not repeating a warning), so it's asked again every cycle rather than its answer kept.
    """

    def __init__(self, layouts: Iterable[Layout]):
        self._by_canvas: dict[Canvas, list[Layout]] = {}
        for layout in layouts:
            for canvas in layout.canvases:
                self._by_canvas.setdefault(canvas, []).append(layout)
        if DEFAULT_CANVAS not in self._by_canvas:
            msg = f"No layout for the default canvas ({DEFAULT_CANVAS})"
            raise ValueError(msg)
        for canvas, candidates in self._by_canvas.items():
            for i, a in enumerate(candidates):
                for b in candidates[i + 1 :]:
                    if a.layout_id == b.layout_id and (a.requires, a.excludes) == (b.requires, b.excludes):
                        msg = f"{type(a).__name__} and {type(b).__name__} both fit the same APIs on {canvas}"
                        raise ValueError(msg)
        self._warned: set[tuple[Canvas, str | None]] = set()

    def layout_ids(self, canvas: Canvas) -> list[str]:
        """The layouts the config can choose from for `canvas`, the default first."""
        return list(dict.fromkeys(layout.layout_id for layout in self._by_canvas.get(canvas, [])))

    def drawable(self, canvas: Canvas) -> Canvas:
        """`canvas` if some layout draws on it, else the default canvas, so a screen nothing
        has been designed for yet still shows the dashboard the way it always has.
        """
        if canvas in self._by_canvas:
            return canvas
        self._warn_once(canvas, None, f"No layout draws on {canvas}; using {DEFAULT_CANVAS}")
        return DEFAULT_CANVAS

    def select(self, canvas: Canvas, layout_id: str | None, available: frozenset[ClientClasses]) -> Layout:
        """The layout to draw this cycle on `canvas` (one drawable() returned): the chosen
        layout, else the canvas's first, in its variant for what's `available`. A layout id
        the canvas doesn't have gets the default, so a choice gone stale never blanks the screen.
        """
        candidates = self._by_canvas[canvas]

        ids = list(dict.fromkeys(layout.layout_id for layout in candidates))
        if layout_id not in ids:
            if layout_id is not None:
                self._warn_once(canvas, layout_id, f"No layout {layout_id!r} for {canvas}; using {ids[0]!r}")
            layout_id = ids[0]

        variants = [layout for layout in candidates if layout.layout_id == layout_id]
        return next((layout for layout in variants if layout.fits(available)), variants[0])

    def _warn_once(self, canvas: Canvas, layout_id: str | None, message: str) -> None:
        # Asked every cycle: once per config is plenty.
        if (canvas, layout_id) not in self._warned:
            self._warned.add((canvas, layout_id))
            logger.warning(message)
