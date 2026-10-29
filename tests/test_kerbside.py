"""The kerbside layouts: the weather in brief over one stop, in the track, ledger and queue styles."""

from typing import TYPE_CHECKING

import pytest
from PIL import Image
from test_display_snapshots import (
    BUS_STOP,
    BUSY_BUS_ARRIVALS,
    BUSY_TUBE_ARRIVALS,
    EVERYTHING,
    HD_VERTICAL,
    NO_GLOWMARKT,
    TUBE_STOP,
    VERTICAL,
    _panels,
    draw_screen,
)
from tfl_factory import tfl_panel
from weather_factory import make_weather

from countdown_renderer.api_registry import ClientClasses
from countdown_renderer.departures.departures_client import DeparturesClient
from countdown_renderer.rendering import LAYOUTS, layout_for, stops_fitting
from countdown_renderer.rendering.layouts.hd_portrait import HD_PORTRAIT
from countdown_renderer.rendering.layouts.kerbside import (
    KERBSIDE_LAYOUTS,
    PORTRAIT,
    LedgerLayout,
    QueueLayout,
    TrackLayout,
)
from countdown_renderer.rendering.screen import DEFAULT_CANVAS
from countdown_renderer.tfl.combined_arrival_panel import CombinedArrivalPanel, Rotation
from countdown_renderer.tfl.kerbside_panel import KERBSIDE_STYLES, KerbsidePanel
from countdown_renderer.weather.kerbside_weather_panel import KerbsideWeatherPanel
from countdown_renderer.weather.weather_client import WeatherClient
from countdown_renderer.weather.weather_panel import WeatherPanel
from countdown_shared.weather.models import WeatherPeriod

if TYPE_CHECKING:
    from countdown_api.models import Screen

LAYOUT_CLASSES = {"track": TrackLayout, "ledger": LedgerLayout, "queue": QueueLayout}


def _screen(layout: str, *, hd: bool = False) -> Screen:
    return (HD_VERTICAL if hd else VERTICAL).model_copy(update={"layout": layout})


FIRST_OF_TWO = Rotation(0, 2)


def _bus_stop(rotation: Rotation | None = FIRST_OF_TWO) -> CombinedArrivalPanel:
    return CombinedArrivalPanel([tfl_panel(BUS_STOP, BUSY_BUS_ARRIVALS)], rotation)


def _station() -> CombinedArrivalPanel:
    return CombinedArrivalPanel([tfl_panel(TUBE_STOP, BUSY_TUBE_ARRIVALS)])


# --- Choosing a layout ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("hd", [False, True], ids=["portrait", "hd"])
@pytest.mark.parametrize("layout_id", KERBSIDE_STYLES)
def test_the_config_picks_each_kerbside_layout_on_both_portrait_canvases(layout_id, hd):
    layout, canvas = layout_for(_screen(layout_id, hd=hd), EVERYTHING)

    assert canvas == (HD_PORTRAIT if hd else PORTRAIT)
    assert type(layout) is LAYOUT_CLASSES[layout_id]
    assert layout.layout_id == layout_id


def test_the_kerbside_layouts_have_one_variant_each_whatever_can_be_shown():
    """They draw only the weather and a stop, so Spotify and the energy chart make no difference."""
    for layout in KERBSIDE_LAYOUTS:
        assert layout.fits(EVERYTHING)
        assert layout.fits(frozenset())


def test_the_portrait_canvas_offers_the_classic_layout_then_the_kerbside_layouts():
    assert LAYOUTS.layout_ids(PORTRAIT) == ["classic", "track", "ledger", "queue"]
    assert not {"track", "ledger", "queue"} & set(LAYOUTS.layout_ids(DEFAULT_CANVAS))


def test_a_kerbside_layout_shows_one_stop_at_a_time():
    screen = _screen("queue")
    assert stops_fitting(_panels(_bus_stop()), screen, EVERYTHING) == 1


# --- Drawing -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("layout_id", KERBSIDE_STYLES)
def test_each_kerbside_layout_on_a_portrait_screen(snapshot, layout_id):
    """A bus stop, the second page of two, under the weather now: the 7.5" screen hung portrait."""
    img = draw_screen(_panels(_bus_stop(Rotation(1, 2))), _screen(layout_id), NO_GLOWMARKT)
    assert img.size == PORTRAIT.size
    snapshot.assert_matches(f"screen_portrait_layout_{layout_id}", img)


@pytest.mark.parametrize("layout_id", KERBSIDE_STYLES)
def test_each_kerbside_layout_on_an_hd_portrait_screen(snapshot, layout_id):
    """A station, whose destinations are long, drawn 2.6 times as big on the HD screen hung portrait."""
    img = draw_screen(_panels(_station()), _screen(layout_id, hd=True), EVERYTHING)
    assert img.size == HD_PORTRAIT.size
    snapshot.assert_matches(f"screen_hd_portrait_layout_{layout_id}", img)


def test_the_weather_is_the_now_page_whichever_page_the_carousel_is_on():
    tonight = make_weather(period=WeatherPeriod.TONIGHT, temperature=9)
    now = make_weather(temperature=14)
    panel = WeatherPanel(tonight, pages=(now, tonight))

    assert TrackLayout._now(panel) is now
    assert TrackLayout._now(WeatherPanel(tonight)) is tonight


def test_messages_take_the_weathers_and_the_stops_places():
    panels = _panels(
        DeparturesClient.message_panel("Needs configuring"), weather=WeatherClient.message_panel("Could not connect")
    )
    img = draw_screen(panels, _screen("ledger"), NO_GLOWMARKT)
    assert img.size == PORTRAIT.size
    assert img.convert("L").getbbox() is not None


def test_a_departures_panel_with_no_stops_draws_only_the_weather():
    panels = _panels(CombinedArrivalPanel([]))
    img = draw_screen(panels, _screen("track"), NO_GLOWMARKT)
    # Nothing drawn under the weather.
    assert img.crop((0, 300, 480, 800)).convert("L").getextrema() == (255, 255)


@pytest.mark.parametrize("style", KERBSIDE_STYLES)
def test_a_stop_with_nothing_leaving_draws_only_its_header(style):
    panel = KerbsidePanel(tfl_panel(BUS_STOP, []), style)
    img = panel.render(452, 508)
    box = img.getbbox()
    assert box is not None
    assert box[3] <= 48


def test_the_queue_lists_every_departure_soonest_first_and_the_ledger_each_route_once():
    stop = tfl_panel(BUS_STOP, BUSY_BUS_ARRIVALS)
    queue, ledger = KerbsidePanel(stop, "queue"), KerbsidePanel(stop, "ledger")
    tall = Image.Image.getbbox(queue.render(452, 2000))
    short = Image.Image.getbbox(ledger.render(452, 2000))
    assert tall is not None
    assert short is not None
    # Seven departures on five routes: the queue draws more rows, so it reaches further down.
    assert tall[3] > short[3]


def test_due_departures_say_due_without_a_unit():
    due = [arrival.model_copy(update={"time_to_station": 0}) for arrival in BUSY_BUS_ARRIVALS[:1]]
    panel = KerbsidePanel(tfl_panel(BUS_STOP, due + BUSY_BUS_ARRIVALS[1:]), "queue")
    assert panel.render(452, 508).getbbox() is not None


def test_a_departure_beyond_the_half_hour_is_off_the_track_but_its_minutes_still_show():
    late = [arrival.model_copy(update={"time_to_station": 45 * 60}) for arrival in BUSY_BUS_ARRIVALS[:1]]
    panel = KerbsidePanel(tfl_panel(BUS_STOP, late), "track")
    assert panel.render(452, 508).getbbox() is not None


def test_the_weather_in_brief_leaves_out_the_rain_when_it_is_not_forecast():
    with_rain = KerbsideWeatherPanel(make_weather()).render(452, 250)
    without = KerbsideWeatherPanel(make_weather(precipitation_probability=None)).render(452, 250)
    rain_box, no_rain_box = with_rain.getbbox(), without.getbbox()
    assert rain_box is not None
    assert no_rain_box is not None
    assert no_rain_box[2] - no_rain_box[0] < rain_box[2] - rain_box[0]


def test_the_developer_panel_changes_nothing_in_a_kerbside_layout():
    layout, _canvas = layout_for(_screen("queue"), EVERYTHING - {ClientClasses.SPOTIFY} | {ClientClasses.DEVELOPER})
    assert type(layout) is QueueLayout
