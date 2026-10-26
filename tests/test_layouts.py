import countdown_core  # noqa: F401, I001 -- see test_display_snapshots.py for why this comes first
import logging
from datetime import datetime

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageOps
from weather_factory import forecast_json, make_weather

from countdown_api.models import Screen
from countdown_core.core.api_registry import ClientClasses
from countdown_core.core.panel import Panel
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import HdNoticeBoardPanel, NoticeBoardPanel
from countdown_core.rendering import LAYOUTS, layout_for, to_native
from countdown_core.rendering.layout import Box, Frame, Layout
from countdown_core.rendering.layouts.classic import CLASSIC, BaseLayout, GlowLayout, SpotifyGlowLayout, SpotifyLayout
from countdown_core.rendering.layouts.classic_portrait import CLASSIC_PORTRAIT
from countdown_core.rendering.layouts.classic_portrait import (
    BasePortraitLayout,
    GlowPortraitLayout,
    SpotifyGlowPortraitLayout,
    SpotifyPortraitLayout,
)
from countdown_core.rendering.layouts.hd_landscape import ARRIVALS_SCALE as HD_ARRIVALS_SCALE
from countdown_core.rendering.layouts.hd_landscape import (
    HD_LANDSCAPE,
    HD_LANDSCAPE_LAYOUTS,
    HOURS_HEIGHT,
    MARGIN,
    SIDEBAR_WIDTH,
    SPOTIFY_HEIGHT as HD_LANDSCAPE_SPOTIFY_HEIGHT,
    WIDE_SPOTIFY_HEIGHT,
    BaseHdLandscapeLayout,
    GlowHdLandscapeLayout,
    HdLandscapeLayout,
    SpotifyGlowHdLandscapeLayout,
    SpotifyHdLandscapeLayout,
)
from countdown_core.rendering.layouts.hd_portrait import SPOTIFY_HEIGHT as HD_SPOTIFY_HEIGHT
from countdown_core.rendering.layouts.hd_portrait import (
    ARRIVALS_SCALE,
    HD_PORTRAIT,
    HD_PORTRAIT_LAYOUTS,
    NOTICES_GAP,
    SCALE,
    WEATHER_HEIGHT,
    HdPortraitLayout,
    BaseHdPortraitLayout,
    GlowHdPortraitLayout,
    SpotifyGlowHdPortraitLayout,
    SpotifyHdPortraitLayout,
)
from countdown_core.rendering.registry import LayoutRegistry
from countdown_core.tfl.combined_arrival_panel import COLUMN_GAP, STOP_WIDTH, CombinedArrivalPanel
from countdown_core.rendering.screen import DEFAULT_CANVAS, Canvas, canvas_for
from countdown_core.utils.scaled_draw import drawing_scale
from countdown_core.utils.utils import GAP
from countdown_core.spotify.models import Queue
from countdown_core.spotify.spotify_panel import HdSpotifyPanel, SpotifyPanel
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.weather.hd_sidebar_panel import HdHoursPanel, HdSidebarPanel
from countdown_core.weather.hd_weather_panel import HdWeatherPanel
from countdown_core.weather.models import ForecastResponse
from countdown_core.weather.weather_forecast import weather_pages
from countdown_core.weather.weather_panel import WeatherPanel
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from countdown_core.utils.clock import ClockFormat

# What only the arrivals are drawn in: not the white background, the black panels or any anti-aliased grey text.
_ARRIVALS_COLOUR = (200, 120, 30)
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


def test_only_the_variants_without_spotify_show_four_stops():
    assert {type(layout): layout.stops_shown for layout in CLASSIC} == {
        SpotifyGlowLayout: 2,
        SpotifyLayout: 2,
        GlowLayout: 4,
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
        ("vertical", 1872, 1404, Canvas(1404, 1872)),
        ("horizontal", 1872, 1404, Canvas(1872, 1404)),
    ],
)
def test_the_canvas_is_the_screen_as_hung(orientation, width, height, expected):
    assert (
        canvas_for(Screen(orientation=orientation, width=width, height=height, format="mono-1bpp-packed")) == expected
    )


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


def test_a_portrait_layout_draws_a_portrait_frame():
    panels = _solid_panels()
    for layout in CLASSIC_PORTRAIT:
        assert layout.render(panels, PORTRAIT).image.size == PORTRAIT.size


@pytest.mark.parametrize(
    ("unavailable", "expected"),
    [
        (set(), SpotifyGlowHdPortraitLayout),
        ({ClientClasses.GLOWMARKT}, SpotifyHdPortraitLayout),
        ({ClientClasses.SPOTIFY}, GlowHdPortraitLayout),
        ({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT}, BaseHdPortraitLayout),
    ],
)
def test_an_hd_portrait_screen_gets_the_classic_layout_stacked(unavailable, expected):
    assert LAYOUTS.drawable(HD_PORTRAIT) == HD_PORTRAIT
    layout = LAYOUTS.select(HD_PORTRAIT, None, EVERYTHING - unavailable)
    assert type(layout) is expected
    # Without Spotify (not set up, or nothing playing), a second row of stops if the notices leave room for it.
    assert layout.stops_shown == (4 if ClientClasses.SPOTIFY in unavailable else 2)


def test_an_hd_portrait_layout_draws_an_hd_frame():
    panels = _solid_panels()
    for layout in HD_PORTRAIT_LAYOUTS:
        assert layout.render(panels, HD_PORTRAIT).image.size == HD_PORTRAIT.size


def test_an_hd_portrait_frame_is_turned_into_the_panels_landscape_picture():
    screen = Screen(orientation="vertical", width=1872, height=1404, format="png")

    native = to_native(Frame(Image.new("RGB", HD_PORTRAIT.size)), screen)

    assert native.image.size == (1872, 1404)


def _arrivals_box(layout: Layout, canvas: Canvas, panels: dict[str, Panel], **kwargs: ClockFormat) -> Box:
    """Where the layout drew the arrivals: the bounding box of the one colour only they are painted in."""
    image = layout.render(panels, canvas, **kwargs).image.convert("RGB")
    off = ImageChops.difference(image, Image.new("RGB", image.size, _ARRIVALS_COLOUR))
    off_by_most = ImageChops.lighter(ImageChops.lighter(*off.split()[:2]), off.getchannel("B"))
    box = off_by_most.point(lambda value: 255 if value == 0 else 0).getbbox()
    assert box is not None, "the arrivals were not drawn"
    return box


def _arrivals_height(layout: Layout, canvas: Canvas, panels: dict[str, Panel], **kwargs: ClockFormat) -> int:
    box = _arrivals_box(layout, canvas, panels, **kwargs)
    return box[3] - box[1]


def test_portrait_energy_chart_leaves_the_notices_the_room_they_had_under_the_full_weather():
    panels = _solid_panels() | {"notice_board": _notices(20)}
    for with_energy, without in [
        (SpotifyGlowPortraitLayout(), SpotifyPortraitLayout()),
        (GlowPortraitLayout(), BasePortraitLayout()),
    ]:
        assert _arrivals_box(with_energy, PORTRAIT, panels)[3] == _arrivals_box(without, PORTRAIT, panels)[3]


@pytest.mark.parametrize("layout", [*CLASSIC, *CLASSIC_PORTRAIT], ids=type)
@pytest.mark.parametrize("clock_format", ["12h", "24h"])
def test_fewer_notices_give_the_arrivals_more_room(layout, clock_format):
    canvas = DEFAULT_CANVAS if layout in CLASSIC else PORTRAIT

    def arrivals_height(notices: Panel) -> int:
        return _arrivals_height(layout, canvas, _solid_panels() | {"notice_board": notices}, clock_format=clock_format)

    full, two, none = arrivals_height(_notices(20)), arrivals_height(_notices(2)), arrivals_height(_notices(0))
    assert full == layout.ARRIVALS_HEIGHT  # a full board keeps the room it always had
    assert full < two < none
    assert none - two == _notices(2).fitted_height(1000) + GAP  # the board and the gap above it


# Each canvas's variants without Spotify: the music isn't set up, or nothing is playing (ApiRegistry.available).
WITHOUT_SPOTIFY = [
    (GlowLayout(), DEFAULT_CANVAS),
    (BaseLayout(), DEFAULT_CANVAS),
    (GlowPortraitLayout(), PORTRAIT),
    (BasePortraitLayout(), PORTRAIT),
    (GlowHdPortraitLayout(), HD_PORTRAIT),
    (BaseHdPortraitLayout(), HD_PORTRAIT),
    (GlowHdLandscapeLayout(), HD_LANDSCAPE),
    (BaseHdLandscapeLayout(), HD_LANDSCAPE),
]


def _drawn_arrivals_height(layout: Layout, canvas: Canvas, panels: dict[str, Panel]) -> float:
    """How tall the layout drew the arrivals, in their own pixels (an HD layout draws them bigger)."""
    scale = (
        1 if canvas in {DEFAULT_CANVAS, PORTRAIT} else ARRIVALS_SCALE if canvas == HD_PORTRAIT else HD_ARRIVALS_SCALE
    )
    return _arrivals_height(layout, canvas, panels) / scale


@pytest.mark.parametrize(("layout", "canvas"), WITHOUT_SPOTIFY, ids=lambda value: type(value).__name__)
def test_without_spotify_a_second_row_of_stops_fits_when_the_notices_leave_room(layout, canvas):
    panels = _solid_panels() | {"notice_board": _notices(2)}

    assert layout.stops_fitting(panels, canvas) == 4
    assert _drawn_arrivals_height(layout, canvas, panels) >= layout.TWO_ROWS_HEIGHT


@pytest.mark.parametrize(
    ("layout", "canvas"), [WITHOUT_SPOTIFY[i] for i in (0, 1, 3, 5, 6)], ids=lambda value: type(value).__name__
)
def test_the_notices_come_before_the_second_row_of_stops(layout, canvas):
    """A full board takes all the room it may have, and the stops are one row.

    (The rest leave room for two rows even then: the compact weather beside the energy chart is shorter, and the HD
    landscape screen without the chart has its row.)
    """
    panels = _solid_panels() | {"notice_board": _notices(20)}

    assert layout.stops_fitting(panels, canvas) == 2


@pytest.mark.parametrize(("layout", "canvas"), WITHOUT_SPOTIFY, ids=lambda value: type(value).__name__)
@pytest.mark.parametrize("notices", [0, 4, 6, 8, 10, 20])
def test_a_second_row_of_stops_whenever_they_are_drawn_tall_enough(layout, canvas, notices):
    panels = _solid_panels() | {"notice_board": _notices(notices)}

    two_rows = _drawn_arrivals_height(layout, canvas, panels) >= layout.TWO_ROWS_HEIGHT
    assert layout.stops_fitting(panels, canvas) == (4 if two_rows else 2)


@pytest.mark.parametrize(("layout", "canvas"), WITHOUT_SPOTIFY, ids=lambda value: type(value).__name__)
@pytest.mark.parametrize("notices", [0, 4, 6, 8, 10, 20])
def test_the_stops_are_as_tall_as_stops_fitting_reckons(layout, canvas, notices):
    """stops_fitting works the stops' height out without drawing: it's the height render() draws them."""
    panels = _solid_panels() | {"notice_board": _notices(notices)}

    assert abs(_drawn_arrivals_height(layout, canvas, panels) - layout._arrivals_height(panels, canvas)) <= 1


@pytest.mark.parametrize(
    ("layout", "canvas"),
    [(layout, DEFAULT_CANVAS) for layout in CLASSIC[:2]]
    + [(layout, PORTRAIT) for layout in CLASSIC_PORTRAIT[:2]]
    + [(layout, HD_PORTRAIT) for layout in HD_PORTRAIT_LAYOUTS[:2]]
    + [(layout, HD_LANDSCAPE) for layout in HD_LANDSCAPE_LAYOUTS[:2]],
    ids=lambda value: type(value).__name__,
)
def test_beside_spotify_the_stops_are_one_row_however_few_the_notices(layout, canvas):
    assert layout.stops_fitting(_solid_panels() | {"notice_board": _notices(0)}, canvas) == 2


@pytest.mark.parametrize("layout", [*CLASSIC[:2], *CLASSIC_PORTRAIT[:2]], ids=type)
def test_developer_strip_above_spotify_takes_its_height_from_the_arrivals_room(layout):
    """The strip and its gap come out of what the notices and the arrivals share, Spotify's room stays."""
    canvas = DEFAULT_CANVAS if layout in CLASSIC else PORTRAIT
    no_notices = _solid_panels() | {"notice_board": _notices(0)}

    strip = _FittedPanel(30)
    alone = _arrivals_height(layout, canvas, no_notices)
    stacked = _arrivals_height(layout, canvas, no_notices | {ClientClasses.DEVELOPER.api_name: strip})
    assert alone - stacked == 30 + GAP


def _solid_panels() -> dict[str, Panel]:
    """Return a panel for every API, as rendering.place_developer_panel hands them on with no song showing.

    So no developer panel under its own name: it would be the strip above Spotify (see test_developer_strip_*).
    """
    panels = {api.api_name: _SolidPanel() for api in ClientClasses if api is not ClientClasses.DEVELOPER}
    return panels | {ClientClasses.TFL.api_name: _SolidPanel(_ARRIVALS_COLOUR)}


def _notices(count: int) -> NoticeBoardPanel:
    return NoticeBoardPanel([Notice("TfL", Severity.INFO, "Victoria: Minor Delays")] * count)


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
    default = layout.render(panels, canvas)
    assert "Until 21:07" in texts
    texts.clear()
    layout.render(panels, canvas, clock_format="12h")
    assert "Until 9:07 PM" in texts
    if layout in CLASSIC and layout.SHOWS_ENERGY:
        assert {"12:07 AM", "12:07 PM"} <= set(texts)
    assert default.image.tobytes() == layout.render(panels, canvas).image.tobytes()
    assert notices.fitted_height(1000) < 100


class _SolidPanel(Panel):
    def __init__(self, colour: tuple[int, int, int] = (0, 0, 0)) -> None:
        self.colour = colour

    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        return Image.new("RGBA", (image_width, image_height), self.colour)


class _FittedPanel(_SolidPanel):
    """A panel only `height` tall, as the developer strip is."""

    def __init__(self, height: int) -> None:
        super().__init__()
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


VERTICAL = Screen(orientation="vertical", width=800, height=480, format="mono-1bpp-packed")
HORIZONTAL = Screen(orientation="horizontal", width=800, height=480, format="mono-1bpp-packed")


def test_a_landscape_frame_goes_to_the_panel_as_it_is():
    frame = Frame(Image.new("RGB", (800, 480)))

    assert to_native(frame, HORIZONTAL) is frame


def test_a_portrait_frame_is_turned_into_the_panels_landscape_picture():
    image = Image.new("RGB", (480, 800), "white")
    image.paste("black", (10, 20, 110, 70))  # a 100x50 block, top-left of the portrait picture

    native = to_native(Frame(image), VERTICAL)

    assert native.image.size == (800, 480)
    # The block moved with the picture: now 50 wide and 100 tall, at the bottom left.
    assert ImageOps.invert(native.image).getbbox() == (20, 370, 70, 470)


class _ScalingPanel(_SolidPanel):
    """A solid panel that draws itself bigger at the drawing scale, as the converted panels do."""

    scales = True

    def __init__(self, colour: tuple[int, int, int] = (0, 0, 0)) -> None:
        super().__init__(colour)
        self.scales_seen: list[float] = []

    def render(self, image_width: int, image_height: int, *, clock_format: ClockFormat = "24h") -> Image.Image:
        self.scales_seen.append(drawing_scale())
        return super().render(round(image_width * drawing_scale()), round(image_height * drawing_scale()))


def test_a_panel_that_scales_draws_itself_bigger_and_any_other_has_its_pixels_repeated():
    img = Image.new("RGBA", (40, 20), "white")
    scaling, solid = _ScalingPanel(), _SolidPanel((10, 20, 30))

    HdPortraitLayout._place_scaled(img, scaling, (0, 0), (5, 5), "24h", 3)
    HdPortraitLayout._place_scaled(img, solid, (20, 0), (5, 5), "24h", 2)

    assert scaling.scales_seen == [3]
    assert drawing_scale() == 1  # back to the 7.5" screens' once it's drawn
    assert img.getpixel((14, 14)) == (0, 0, 0, 255)
    assert img.getpixel((15, 15)) == (255, 255, 255, 255)
    assert img.getpixel((29, 9)) == (10, 20, 30, 255)
    assert img.getpixel((30, 10)) == (255, 255, 255, 255)


def test_the_stops_on_an_hd_portrait_screen_are_drawn_two_and_a_half_times_as_big_across_its_width():
    stops = CombinedArrivalPanel([_ScalingPanel(_ARRIVALS_COLOUR), _ScalingPanel(_ARRIVALS_COLOUR)])
    panels = _solid_panels() | {ClientClasses.TFL.api_name: stops}

    box = _arrivals_box(BaseHdPortraitLayout(), HD_PORTRAIT, panels)

    # Two stops and the gap between them, from the left gap, two and a half times as big: each starts and ends on the
    # screen pixel nearest where its own would.
    stop_width = min(STOP_WIDTH, (int((HD_PORTRAIT.width - 4 * GAP) / ARRIVALS_SCALE) - COLUMN_GAP) // 2)
    second_stop_x = round((stop_width + COLUMN_GAP) * ARRIVALS_SCALE)
    assert (box[0], box[2]) == (2 * GAP, 2 * GAP + second_stop_x + round(stop_width * ARRIVALS_SCALE))


Placed = tuple[Panel, tuple[int, int], tuple[int, int], float]


def _placed(monkeypatch) -> list[Placed]:
    """Record every panel the HD layout places: where (in screen pixels), how big (in its own) and at which scale."""
    placed: list[Placed] = []
    place = HdPortraitLayout._place_scaled

    def record(img, panel, at, size, clock_format, scale=SCALE):  # noqa: PLR0913, PLR0917 -- _place_scaled's signature
        placed.append((panel, at, size, scale))
        place(img, panel, at, size, clock_format, scale)

    monkeypatch.setattr(HdPortraitLayout, "_place_scaled", staticmethod(record))
    return placed


def test_the_hd_weather_spans_the_screen_in_the_configs_design_at_the_stops_scale(monkeypatch):
    placed = _placed(monkeypatch)
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    panels = _solid_panels() | {ClientClasses.WEATHER.api_name: WeatherPanel(pages[1], "poster", pages)}

    BaseHdPortraitLayout().render(panels, HD_PORTRAIT)

    [(panel, _at, size, scale)] = [entry for entry in placed if isinstance(entry[0], HdWeatherPanel)]
    assert (panel.weather, panel.design, panel.pages) == (pages[1], "poster", tuple(pages))
    assert scale == ARRIVALS_SCALE
    assert size == (int((HD_PORTRAIT.width - 4 * GAP) / ARRIVALS_SCALE), int(WEATHER_HEIGHT / ARRIVALS_SCALE))


@pytest.mark.parametrize(
    ("layout", "weather"),
    [
        (BaseHdPortraitLayout(), _SolidPanel()),  # a message, not a forecast
        (GlowHdPortraitLayout(), WeatherPanel(make_weather(), "poster")),  # half the row, beside the energy chart
    ],
    ids=["message", "beside the energy chart"],
)
def test_otherwise_the_hd_weather_is_the_portrait_panel_twice_as_big(monkeypatch, layout, weather):
    placed = _placed(monkeypatch)

    layout.render(_solid_panels() | {ClientClasses.WEATHER.api_name: weather}, HD_PORTRAIT)

    assert not any(isinstance(panel, HdWeatherPanel) for panel, _at, _size, _scale in placed)
    [(_panel, _at, _size, scale)] = [entry for entry in placed if entry[0] is weather]
    assert scale == SCALE


def _queue() -> Queue:
    track = {"name": "A Song", "artists": [{"name": "An Artist"}], "album": {"name": "An Album", "images": []}}
    return Queue.model_validate({"currently_playing": {**track, "duration_ms": 1}, "queue": []})


def test_the_hd_screen_draws_spotifys_now_page_wide_and_the_notice_board_as_a_table(monkeypatch):
    placed = _placed(monkeypatch)
    spotify, notices = SpotifyPanel(_queue(), "card"), _notices(2)
    panels = _solid_panels() | {ClientClasses.SPOTIFY.api_name: spotify, ClientClasses.NOTICE_BOARD.api_name: notices}

    SpotifyHdPortraitLayout().render(panels, HD_PORTRAIT)

    [(hd_spotify, spotify_at, spotify_size, _)] = [entry for entry in placed if isinstance(entry[0], HdSpotifyPanel)]
    [(hd_notices, notices_at, _, _)] = [entry for entry in placed if isinstance(entry[0], HdNoticeBoardPanel)]
    assert (hd_spotify.queue, hd_spotify.style) == (spotify.queue, "card")
    assert spotify_size[1] == HD_SPOTIFY_HEIGHT
    assert (hd_notices.notices, hd_notices.style) == (notices.notices, notices.style)
    assert notices_at[1] == spotify_at[1] + SCALE * (HD_SPOTIFY_HEIGHT + NOTICES_GAP)  # set apart by a bigger gap
    assert panels[ClientClasses.SPOTIFY.api_name] is spotify  # the panels handed in are left as they were


def test_anything_else_in_spotifys_and_the_notices_areas_is_drawn_as_it_is(monkeypatch):
    placed = _placed(monkeypatch)
    panels = _solid_panels() | {ClientClasses.NOTICE_BOARD.api_name: _FittedPanel(40)}

    SpotifyHdPortraitLayout().render(panels, HD_PORTRAIT)

    assert not any(isinstance(panel, HdSpotifyPanel | HdNoticeBoardPanel) for panel, *_ in placed)
    assert {panels[ClientClasses.SPOTIFY.api_name], panels[ClientClasses.NOTICE_BOARD.api_name]} <= {
        panel for panel, *_ in placed
    }


@pytest.mark.parametrize(
    ("unavailable", "expected"),
    [
        (set(), SpotifyGlowHdLandscapeLayout),
        ({ClientClasses.GLOWMARKT}, SpotifyHdLandscapeLayout),
        ({ClientClasses.SPOTIFY}, GlowHdLandscapeLayout),
        ({ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT}, BaseHdLandscapeLayout),
    ],
)
def test_an_hd_landscape_screen_gets_the_sidebar_layout(unavailable, expected):
    assert LAYOUTS.drawable(HD_LANDSCAPE) == HD_LANDSCAPE
    layout = LAYOUTS.select(HD_LANDSCAPE, None, EVERYTHING - unavailable)
    assert type(layout) is expected
    assert layout.layout_id == "sidebar"


def test_an_hd_landscape_screen_offers_the_sidebar_first_then_its_other_designs():
    assert LAYOUTS.layout_ids(HD_LANDSCAPE) == ["sidebar", "board", "column", "focus", "newspaper", "pages"]


@pytest.mark.parametrize("layout_id", ["board", "column", "focus", "newspaper", "pages"])
def test_the_config_picks_each_hd_landscape_design(layout_id):
    screen = Screen(orientation="horizontal", width=1872, height=1404, format="png", layout=layout_id)
    layout, canvas = layout_for(screen, EVERYTHING)
    assert (layout.layout_id, canvas) == (layout_id, HD_LANDSCAPE)


@pytest.mark.parametrize("layout_id", ["masthead", "lockscreen", "timeline", "classic"])
def test_a_portrait_only_layout_on_an_hd_landscape_screen_gets_the_sidebar(layout_id):
    screen = Screen(orientation="horizontal", width=1872, height=1404, format="png", layout=layout_id)
    assert layout_for(screen, EVERYTHING)[0].layout_id == "sidebar"


def test_an_hd_landscape_layout_draws_an_hd_frame_as_the_panel_takes_it():
    screen = Screen(orientation="horizontal", width=1872, height=1404, format="png")
    for layout in HD_LANDSCAPE_LAYOUTS:
        frame = layout.render(_solid_panels(), HD_LANDSCAPE)
        assert frame.image.size == HD_LANDSCAPE.size
        assert to_native(frame, screen) is frame


def test_the_stops_on_an_hd_landscape_screen_just_fill_the_column_beside_the_sidebar():
    stops = CombinedArrivalPanel([_ScalingPanel(_ARRIVALS_COLOUR), _ScalingPanel(_ARRIVALS_COLOUR)])
    panels = _solid_panels() | {ClientClasses.TFL.api_name: stops}

    box = _arrivals_box(BaseHdLandscapeLayout(), HD_LANDSCAPE, panels)

    assert (box[0], box[2]) == (SIDEBAR_WIDTH + MARGIN, HD_LANDSCAPE.width - MARGIN)


def _placed_landscape(monkeypatch) -> list[Placed]:
    """Record every panel the HD landscape layout places, as _placed does the portrait one's."""
    placed: list[Placed] = []
    place = HdLandscapeLayout._place_scaled

    def record(img, panel, at, size, clock_format, scale=SCALE):  # noqa: PLR0913, PLR0917 -- _place_scaled's signature
        placed.append((panel, at, size, scale))
        place(img, panel, at, size, clock_format, scale)

    monkeypatch.setattr(HdLandscapeLayout, "_place_scaled", staticmethod(record))
    return placed


def test_the_sidebar_has_the_forecasts_pages_and_the_hours_strip_its_coming_hours(monkeypatch):
    placed = _placed_landscape(monkeypatch)
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    panels = _solid_panels() | {ClientClasses.WEATHER.api_name: WeatherPanel(pages[2], "poster", pages)}

    BaseHdLandscapeLayout().render(panels, HD_LANDSCAPE)

    [(sidebar, at, size, _)] = [entry for entry in placed if isinstance(entry[0], HdSidebarPanel)]
    assert (sidebar.pages, at, size) == (tuple(pages), (0, 0), (SIDEBAR_WIDTH // SCALE, HD_LANDSCAPE.height // SCALE))
    [(hours, _, size, _)] = [entry for entry in placed if isinstance(entry[0], HdHoursPanel)]
    assert hours.hours == pages[0].hours
    assert size[1] == HOURS_HEIGHT


def test_without_a_forecast_the_sidebar_says_why_and_the_stops_get_the_hours_room(monkeypatch):
    placed = _placed_landscape(monkeypatch)
    message = MessagePanel("Weather", "Could not connect")
    panels = _solid_panels() | {ClientClasses.WEATHER.api_name: message}

    BaseHdLandscapeLayout().render(panels, HD_LANDSCAPE)

    [sidebar] = [panel for panel, *_ in placed if isinstance(panel, HdSidebarPanel)]
    assert (sidebar.pages, sidebar.message) == ((), ("Weather", "Could not connect"))
    assert not any(isinstance(panel, HdHoursPanel) for panel, *_ in placed)


@pytest.mark.parametrize(
    ("layout", "spotify_size", "energy_width"),
    [
        # Side by side: the energy chart has 45% of the column, Spotify the rest past a gap, at its usual height.
        (SpotifyGlowHdLandscapeLayout(), (611 - 275 - 15, HD_LANDSCAPE_SPOTIFY_HEIGHT), 275),
        # Spotify alone has the row, its now page wide and as tall as the HD portrait screen's.
        (SpotifyHdLandscapeLayout(), (611, WIDE_SPOTIFY_HEIGHT), None),
        (GlowHdLandscapeLayout(), None, 611),
        (BaseHdLandscapeLayout(), None, None),
    ],
    ids=type,
)
def test_the_row_under_the_hours_has_the_energy_chart_and_spotify_as_the_variant_shows(
    monkeypatch, layout, spotify_size, energy_width
):
    placed = _placed_landscape(monkeypatch)
    spotify, energy = SpotifyPanel(_queue(), "hero"), _SolidPanel()
    panels = _solid_panels() | {ClientClasses.SPOTIFY.api_name: spotify, ClientClasses.GLOWMARKT.api_name: energy}

    layout.render(panels, HD_LANDSCAPE)

    spotify_placed = [(panel, size) for panel, _, size, _ in placed if isinstance(panel, SpotifyPanel)]
    assert [size for _, size in spotify_placed] == ([spotify_size] if spotify_size else [])
    if spotify_size:
        [(panel, _)] = spotify_placed
        assert isinstance(panel, HdSpotifyPanel) is (spotify_size[1] == WIDE_SPOTIFY_HEIGHT)
    assert [size[0] for panel, _, size, _ in placed if panel is energy] == ([energy_width] if energy_width else [])


def test_the_stops_get_the_room_an_hd_landscape_variant_does_not_use():
    panels = _solid_panels() | {"notice_board": _notices(2)}

    heights = [_arrivals_height(layout, HD_LANDSCAPE, panels) for layout in HD_LANDSCAPE_LAYOUTS]

    # Everything, then Spotify alone (wide, so taller) and the energy chart alone (as tall), then neither.
    assert heights[1] == heights[2] < heights[0] < heights[3]
    # The energy chart's row and the gap above it, to a screen pixel: the stops' own are 2.25 of them.
    assert heights[3] - heights[2] == pytest.approx(2 * 150 + 30, abs=1)


def test_the_developer_strip_sits_above_spotify_beside_the_energy_chart(monkeypatch):
    placed = _placed_landscape(monkeypatch)
    strip, spotify = _FittedPanel(30), _SolidPanel()
    panels = _solid_panels() | {ClientClasses.DEVELOPER.api_name: strip, ClientClasses.SPOTIFY.api_name: spotify}

    SpotifyGlowHdLandscapeLayout().render(panels, HD_LANDSCAPE)

    [(_, strip_at, strip_size, _)] = [entry for entry in placed if entry[0] is strip]
    [(_, spotify_at, spotify_size, _)] = [entry for entry in placed if entry[0] is spotify]
    [(_, energy_at, energy_size, _)] = [
        entry for entry in placed if entry[0] is panels[ClientClasses.GLOWMARKT.api_name]
    ]
    assert strip_at[0] == spotify_at[0] > energy_at[0]
    assert (strip_size[1], spotify_size[1]) == (30, HD_LANDSCAPE_SPOTIFY_HEIGHT)
    assert spotify_at[1] == strip_at[1] + SCALE * (30 + GAP)
    assert energy_at[1] == strip_at[1]
    assert energy_size[1] == 30 + GAP + HD_LANDSCAPE_SPOTIFY_HEIGHT  # the chart as tall as the two together
