"""What the HD screen's layouts share: the panels it draws its own way, and drawing a panel bigger.

The panels are laid out in pixels sized for the 7.5" screens, so at the HD screen's density (about 227 dpi) they'd be a
third as big. So each is drawn bigger, at the screen's own resolution (see utils.scaled_draw). A panel that can't draw
itself bigger (a message) has its pixels repeated instead.
"""

from typing import TYPE_CHECKING

from PIL import Image

from countdown_core.core.api_registry import ClientClasses
from countdown_core.notices.notice_board_panel import HdNoticeBoardPanel, NoticeBoardPanel
from countdown_core.rendering.layout import Layout
from countdown_core.spotify.spotify_panel import HdSpotifyPanel, SpotifyPanel
from countdown_core.utils.scaled_draw import drawing_at

if TYPE_CHECKING:
    from countdown_core.core.panel import Panel
    from countdown_core.utils.clock import ClockFormat

# How many screen pixels each of a panel's takes, either way, unless the layout says otherwise.
SCALE = 2


class HdLayout(Layout):
    """A layout for the HD screen: its panels drawn bigger, Spotify's now page and the notice board its own way."""

    @staticmethod
    def _hd_panels(panels: dict[str, Panel], *, wide_spotify: bool = True) -> dict[str, Panel]:
        """Return `panels` with the notice board, and Spotify's now page if `wide_spotify`, drawn the HD screen's way.

        Anything else under their names (a message, a top list, the developer panel) is left as it is.
        """
        hd = dict(panels)
        spotify = hd.get(ClientClasses.SPOTIFY.api_name)
        if wide_spotify and isinstance(spotify, SpotifyPanel):
            hd[ClientClasses.SPOTIFY.api_name] = HdSpotifyPanel.of(spotify)
        notices = hd.get(ClientClasses.NOTICE_BOARD.api_name)
        if isinstance(notices, NoticeBoardPanel):
            hd[ClientClasses.NOTICE_BOARD.api_name] = HdNoticeBoardPanel.of(notices)
        return hd

    @staticmethod
    def _place_scaled(  # noqa: PLR0913, PLR0917 -- _place's arguments and the scale
        img: Image.Image,
        panel: Panel,
        at: tuple[int, int],
        size: tuple[int, int],
        clock_format: ClockFormat,
        scale: float = SCALE,
    ) -> None:
        """Render `panel` `size` big in its own pixels, `scale` times as big, and paste it with its top left `at`.

        A panel that `scales` draws itself at that size, crisply. Any other has its pixels repeated, not smoothed: the
        1-bit text keeps its edges, with no grey for the e-paper to dither.
        """
        if panel.scales:
            with drawing_at(scale):
                scaled = panel.render(*size, clock_format=clock_format)
        else:
            rendered = panel.render(*size, clock_format=clock_format)
            scaled = rendered.resize(
                (round(rendered.width * scale), round(rendered.height * scale)), Image.Resampling.NEAREST
            )
        img.paste(scaled, at, scaled)
