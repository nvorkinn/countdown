import countdown_core  # noqa: F401, I001 -- see test_display_snapshots.py for why this comes first
import logging
from datetime import datetime

import pytest
from PIL import Image, ImageDraw, ImageOps

from countdown_api.models import Screen
from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import NoticeBoardPanel
from countdown_core.rendering import LAYOUTS, to_native
from countdown_core.rendering.layout import Frame, Layout
from countdown_core.rendering.layouts.classic import CLASSIC, BaseLayout, GlowLayout, SpotifyGlowLayout, SpotifyLayout
from countdown_core.rendering.layouts.classic_portrait import CLASSIC_PORTRAIT, ENERGY_ROW_HEIGHT
from countdown_core.rendering.layouts.classic_portrait import (
    BasePortraitLayout,
    GlowPortraitLayout,
    SpotifyGlowPortraitLayout,
    SpotifyPortraitLayout,
)
from countdown_core.rendering.registry import LayoutRegistry
from countdown_core.rendering.screen import DEFAULT_CANVAS, Canvas, canvas_for
from countdown_core.utils.utils import GAP
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from countdown_core.utils.clock import ClockFormat

EVERYTHING = frozenset(ClientClasses)
PORTRAIT = Canvas(480, 800)


@pytest.mark.parametrize(
    ("unavailable", "expected"),
    [
        (set(), SpotifyGlowLayout),
        ({ClientClasses.GLOWMARKT}, SpotifyLayout),
        ({ClientClasses.SPOTIFY}, GlowLayout),
        ({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT}, BaseLayout),
    ],
)
def test_the_classic_layout_makes_room_only_for_what_can_be_shown(unavailable, expected):
    assert type(LAYOUTS.select(DEFAULT_CANVAS, None, EVERYTHING - unavailable)) is expected


def test_every_combination_of_spotify_and_glowmarkt_has_exactly_one_classic_variant():
    """So the order of the variants doesn't matter, and the fallback is never needed."""
    optional = [ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT]
    for spotify in (True, False):
        for glowmarkt in (True, False):
            unavailable = {api for api, on in zip(optional, (spotify, glowmarkt), strict=True) if not on}
            fitting = [layout for layout in CLASSIC if layout.fits(EVERYTHING - unavailable)]
            assert len(fitting) == 1, (spotify, glowmarkt, fitting)


def test_only_the_variant_without_spotify_or_glowmarkt_shows_four_stops():
    assert {type(layout): layout.stops_shown for layout in CLASSIC} == {
        SpotifyGlowLayout: 2,
        SpotifyLayout: 2,
        GlowLayout: 2,
        BaseLayout: 4,
    }


@pytest.mark.parametrize(
    ("orientation", "width", "height", "expected"),
    [
        ("horizontal", 800, 480, Canvas(800, 480)),
        ("vertical", 800, 480, Canvas(480, 800)),
        # The device reports the panel the same way round however it's hung, but which way
        # that is doesn't matter: the orientation decides which side is long.
        ("horizontal", 480, 800, Canvas(800, 480)),
        ("vertical", 480, 800, Canvas(480, 800)),
    ],
)
def test_the_canvas_is_the_screen_as_hung(orientation, width, height, expected):
    assert canvas_for(Screen(orientation=orientation, width=width, height=height)) == expected


@pytest.mark.parametrize(
    ("unavailable", "expected"),
    [
        (set(), SpotifyGlowPortraitLayout),
        ({ClientClasses.GLOWMARKT}, SpotifyPortraitLayout),
        ({ClientClasses.SPOTIFY}, GlowPortraitLayout),
        ({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT}, BasePortraitLayout),
    ],
)
def test_a_portrait_screen_gets_the_classic_layout_stacked(unavailable, expected):
    assert LAYOUTS.drawable(PORTRAIT) == PORTRAIT
    assert type(LAYOUTS.select(PORTRAIT, None, EVERYTHING - unavailable)) is expected


def test_portrait_arrivals_are_the_volatile_part():
    panels = _solid_panels()
    for layout in CLASSIC_PORTRAIT:
        frame = layout.render(panels, PORTRAIT)
        top = 5 + ENERGY_ROW_HEIGHT + 15 if layout.SHOWS_ENERGY else 290
        assert frame.image.size == PORTRAIT.size
        assert frame.volatile == (5, top, 475, top + layout.ARRIVALS_HEIGHT)


def test_portrait_energy_chart_leaves_the_notices_the_room_they_had_under_the_full_weather():
    panels = _solid_panels() | {"notice_board": _notices(20)}
    for with_energy, without in [
        (SpotifyGlowPortraitLayout(), SpotifyPortraitLayout()),
        (GlowPortraitLayout(), BasePortraitLayout()),
    ]:
        assert with_energy.render(panels, PORTRAIT).volatile[3] == without.render(panels, PORTRAIT).volatile[3]


def _solid_panels() -> dict[str, Panel]:
    """Return a panel for every API, as rendering.place_developer_panel hands them on with no song showing.

    So no developer panel under its own name: it would be the strip above Spotify (see test_developer_strip_*).
    """
    return {api.api_name: _SolidPanel() for api in ClientClasses if api is not ClientClasses.DEVELOPER}


def _notices(count: int) -> NoticeBoardPanel:
    return NoticeBoardPanel([Notice("TfL", Severity.INFO, "Victoria: Minor Delays")] * count)


@pytest.mark.parametrize("layout", [*CLASSIC, *CLASSIC_PORTRAIT], ids=type)
@pytest.mark.parametrize("clock_format", ["12h", "24h"])
def test_fewer_notices_give_the_arrivals_more_room(layout, clock_format):
    canvas = DEFAULT_CANVAS if layout in CLASSIC else PORTRAIT

    def arrivals_height(notices: Panel) -> int:
        panels = _solid_panels() | {"notice_board": notices}
        box = layout.render(panels, canvas, clock_format=clock_format).volatile
        return box[3] - box[1]

    full, two, none = arrivals_height(_notices(20)), arrivals_height(_notices(2)), arrivals_height(_notices(0))
    assert full == layout.ARRIVALS_HEIGHT  # a full board keeps the room it always had
    assert full < two < none
    assert none - two == _notices(2).fitted_height(1000) + GAP  # the board and the gap above it


@pytest.mark.parametrize("layout", [*CLASSIC, *CLASSIC_PORTRAIT], ids=type)
def test_clock_switching_preserves_compact_notice_fitting_and_reaches_each_layout(layout, monkeypatch):
    canvas = DEFAULT_CANVAS if layout in CLASSIC else PORTRAIT
    expiry = datetime.fromisoformat("2026-09-25T21:07:00+01:00")
    notices = NoticeBoardPanel([Notice("Met Office", Severity.INFO, "Until", expiry, display_time=expiry)])
    panels = _solid_panels() | {
        "notice_board": notices,
        "glowmarkt": EnergyPanel([{"start": f"2026-09-25T{hour:02d}:07:00+01:00", "kwh": 1} for hour in (0, 12)], 0),
    }
    texts = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    full = layout.render(panels | {"notice_board": _notices(20)}, canvas).volatile
    default = layout.render(panels, canvas)
    assert "Until 21:07" in texts
    texts.clear()
    twelve_hour = layout.render(panels, canvas, clock_format="12h")
    assert "Until 9:07 PM" in texts
    if layout in CLASSIC and layout.SHOWS_ENERGY:
        assert {"12:07 AM", "12:07 PM"} <= set(texts)
    assert default.volatile == twelve_hour.volatile
    assert default.volatile[3] > full[3]
    assert default.image.tobytes() == layout.render(panels, canvas).image.tobytes()
    assert notices.fitted_height(1000) < 100


class _SolidPanel(Panel):
    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        return Image.new("RGBA", (image_width, image_height), "black")


class _FittedPanel(_SolidPanel):
    """A panel only `height` tall, as the developer strip is."""

    def __init__(self, height: int) -> None:
        self.height = height

    def fitted_height(self, max_height: int) -> int:
        return min(self.height, max_height)


def test_a_canvas_no_layout_draws_on_falls_back_to_the_default_with_one_warning(caplog):
    caplog.set_level(logging.WARNING)
    registry = LayoutRegistry(CLASSIC)

    assert registry.drawable(PORTRAIT) == DEFAULT_CANVAS
    assert registry.drawable(PORTRAIT) == DEFAULT_CANVAS
    assert registry.drawable(DEFAULT_CANVAS) == DEFAULT_CANVAS

    assert caplog.text.count("No layout draws on 480x800") == 1


class _Solid(Layout):
    canvases = frozenset({DEFAULT_CANVAS})

    def render(self, panels: dict[str, Panel], canvas: Canvas, *, clock_format: ClockFormat = "24h") -> Frame:
        return Frame(Image.new("RGBA", canvas.size))


class _Compact(_Solid):
    layout_id = "compact"


class _CompactNoSpotify(_Solid):
    layout_id = "compact"
    excludes = frozenset({ClientClasses.SPOTIFY})


class _Portrait(_Solid):
    layout_id = "tall"
    canvases = frozenset({PORTRAIT})


def test_the_config_picks_between_layouts_on_the_same_canvas():
    registry = LayoutRegistry([*CLASSIC, _CompactNoSpotify(), _Compact()])

    assert registry.layout_ids(DEFAULT_CANVAS) == ["classic", "compact"]
    assert type(registry.select(DEFAULT_CANVAS, None, EVERYTHING)) is SpotifyGlowLayout
    assert type(registry.select(DEFAULT_CANVAS, "compact", EVERYTHING)) is _Compact
    assert type(registry.select(DEFAULT_CANVAS, "compact", EVERYTHING - {ClientClasses.SPOTIFY})) is _CompactNoSpotify


def test_a_layout_choice_the_canvas_does_not_have_gets_its_default_with_one_warning(caplog):
    caplog.set_level(logging.WARNING)
    registry = LayoutRegistry([*CLASSIC, _Portrait()])

    for _ in range(2):
        assert type(registry.select(DEFAULT_CANVAS, "tall", EVERYTHING)) is SpotifyGlowLayout

    assert caplog.text.count("No layout 'tall' for 800x480") == 1


def test_each_canvas_has_its_own_layouts():
    registry = LayoutRegistry([*CLASSIC, _Portrait()])

    assert registry.drawable(PORTRAIT) == PORTRAIT
    assert type(registry.select(PORTRAIT, None, EVERYTHING)) is _Portrait


def test_two_variants_of_a_layout_that_fit_the_same_apis_are_refused():
    with pytest.raises(ValueError, match="both fit the same APIs"):
        LayoutRegistry([*CLASSIC, _Compact(), _Compact()])


def test_a_registry_with_nothing_for_the_default_canvas_is_refused():
    with pytest.raises(ValueError, match="default canvas"):
        LayoutRegistry([_Portrait()])


VERTICAL = Screen(orientation="vertical", width=800, height=480)
HORIZONTAL = Screen(orientation="horizontal", width=800, height=480)


def test_a_landscape_frame_goes_to_the_panel_as_it_is():
    frame = Frame(Image.new("RGB", (800, 480)), (5, 5, 100, 50))

    assert to_native(frame, HORIZONTAL) is frame


def test_a_portrait_frame_is_turned_into_the_panels_landscape_picture_with_its_volatile_box():
    image = Image.new("RGB", (480, 800), "white")
    image.paste("black", (10, 20, 110, 70))  # a 100x50 block, top-left of the portrait picture
    frame = Frame(image, (10, 20, 110, 70))

    native = to_native(frame, VERTICAL)

    assert native.image.size == (800, 480)
    # The box moved with the picture: it's exactly the black block, now 50 wide and 100 tall.
    assert native.volatile == (20, 370, 70, 470)
    assert native.image.getbbox() is not None
    assert ImageOps.invert(native.image).getbbox() == native.volatile


def test_a_portrait_frame_without_a_volatile_part_has_none_once_turned():
    assert to_native(Frame(Image.new("RGB", (480, 800))), VERTICAL).volatile is None


@pytest.mark.parametrize("layout", [*CLASSIC[:2], *CLASSIC_PORTRAIT[:2]], ids=type)
def test_developer_strip_above_spotify_takes_its_height_from_the_arrivals_room(layout):
    """The strip and its gap come out of what the notices and the arrivals share, Spotify's room stays."""
    canvas = DEFAULT_CANVAS if layout in CLASSIC else PORTRAIT

    def arrivals_height(panels: dict[str, Panel]) -> int:
        box = layout.render(panels | {"notice_board": _notices(0)}, canvas).volatile
        return box[3] - box[1]

    strip = _FittedPanel(30)
    alone = arrivals_height(_solid_panels())
    stacked = arrivals_height(_solid_panels() | {ClientClasses.DEVELOPER.api_name: strip})
    assert alone - stacked == 30 + GAP
