import countdown_renderer  # noqa: F401, I001 -- see test_display_snapshots.py for why this comes first
import datetime as dt

import pytest
from tfl_factory import tfl_panel
from test_utils import assert_text_inside_and_apart
from weather_factory import forecast_json, make_weather

from countdown_renderer import moment
from countdown_renderer.departures.departures_client import DeparturesClient
from countdown_renderer.notices.notice import Notice, Severity
from countdown_renderer.notices.notice_board_panel import NoticeBannerPanel, NoticeBoardPanel
from countdown_shared.tfl.models import BusArrival, MetroStopPoint, SingleStopPoint, TubeArrival
from countdown_renderer.tfl.route_board_panel import BAR, ROW, STOP_GAP, RouteBoardPanel, rows_each
from countdown_renderer.tfl.timeline_panel import WINDOW, TimelinePanel, departures, placed
from countdown_renderer.utils.scaled_draw import drawing_at
from countdown_renderer.weather.hd_clock_panels import (
    SQUARE,
    SQUARE_GAP,
    HdClockPanel,
    HdMastheadPanel,
    HdWeatherColumnPanel,
    clock_and_date,
    high_low,
)
from countdown_renderer.weather.hd_sidebar_panel import current_page, later_rows, readings
from countdown_shared.weather.models import ForecastResponse, WeatherPeriod
from countdown_renderer.weather.weather_forecast import weather_pages

MOMENT = dt.datetime(2026, 1, 15, 12, 0, tzinfo=dt.UTC)
MESSAGE = ("Weather", "Could not connect")
BUS_STOP = SingleStopPoint(
    naptanId="490000123W",
    commonName="Elephant & Castle",
    modes=["bus"],
    additionalProperties=[],
    stopType="NaptanPublicBusCoachTram",
    stopLetter="W",
)
TUBE_STOP = MetroStopPoint(
    naptanId="940GZZLUKNG",
    commonName="Kennington",
    modes=["tube"],
    additionalProperties=[],
    stopType="NaptanMetroStation",
)


@pytest.fixture(autouse=True)
def _at_noon():
    with moment.drawing_at(MOMENT):
        yield


def _bus_stop(rows: list[tuple[str, str, int]], name: str = "Elephant & Castle"):
    arrivals = [
        BusArrival(naptanId="490000123W", lineName=line, timeToStation=minutes * 60, modeName="bus", destinationName=to)
        for line, to, minutes in rows
    ]
    return tfl_panel(BUS_STOP.model_copy(update={"common_name": name}), arrivals)


def _station(rows: list[tuple[str, int]]):
    arrivals = [
        TubeArrival(
            naptanId="940GZZLUKNG", lineName="Northern", timeToStation=minutes * 60, modeName="tube", towards=to
        )
        for to, minutes in rows
    ]
    return tfl_panel(TUBE_STOP, arrivals)


def _pages():
    return weather_pages(ForecastResponse.model_validate(forecast_json()))


def _texts(drawn_text) -> list[str]:
    return [text for text, _box, _size in drawn_text]


# --- The timeline ---------------------------------------------------------------------------------------------------


def test_the_timeline_keeps_the_departures_in_its_window_soonest_first():
    stop = _bus_stop(
        [("36", "Queen's Park", 12), ("185", "Victoria", 4), ("36", "Queen's Park", 31), ("P5", "Oval", 0)]
    )

    assert departures(stop) == [("P5", "Oval", 0), ("185", "Victoria", 4), ("36", "Queen's Park", 12)]
    assert departures(stop, window=WINDOW + 1)[-1] == ("36", "Queen's Park", 31)
    assert departures(stop, window=3) == [("P5", "Oval", 0)]


def test_a_departure_at_the_end_of_the_window_is_in_it():
    assert departures(_bus_stop([("36", "Queen's Park", WINDOW)])) == [("36", "Queen's Park", WINDOW)]


def test_the_timeline_of_a_stop_with_nothing_leaving_is_empty():
    assert departures(_bus_stop([])) == []


def test_each_row_sits_at_its_minute_when_there_is_room():
    assert placed([0, 5, 10], 100, 10, 26) == [100, 150, 200]


def test_a_row_too_close_to_the_one_above_is_pushed_down_to_keep_the_spacing():
    assert placed([0, 1, 2, 10], 100, 10, 26) == [100, 126, 152, 200]
    assert placed([3, 3, 3], 0, 4, 10) == [12, 22, 32]
    assert placed([], 0, 10, 26) == []


def test_the_timeline_draws_at_most_two_stops_and_leaves_out_messages():
    first, second, third = (_bus_stop([("36", "Queen's Park", 2)], name) for name in ("A", "B", "C"))
    message = DeparturesClient.message_panel("Needs configuring")

    assert TimelinePanel([first, second, third]).stops == [first, second]
    assert TimelinePanel([message, first]).stops == [first]
    assert TimelinePanel([message]).stops == []


def test_the_timeline_draws_the_first_two_stops_that_have_departures_to_show():
    empty, first, second = _bus_stop([], "Empty"), _bus_stop([("36", "A", 2)], "A"), _station([("Morden", 3)])

    assert TimelinePanel([empty, first, second]).stops == [first, second]


def test_the_timeline_draws_each_stops_next_half_hour(drawn_text):
    buses = _bus_stop([("36", "Queen's Park", 0), ("185", "Victoria", 9), ("P5", "Peckham", 40)])
    trains = _station([("Morden", 3), ("Edgware", 21)])

    img = TimelinePanel([buses, trains]).render(600, 700)

    assert img.size == (600, 700)
    texts = _texts(drawn_text)
    assert {"Elephant & Castle", "Kennington", "Queen's Park", "Victoria", "Morden", "Edgware"} <= set(texts)
    assert {"due", "9 min", "3 min", "21 min", "NOW"} <= set(texts)
    assert "Peckham" not in texts
    assert "40 min" not in texts


def test_the_timeline_draws_twice_as_big_at_twice_the_scale():
    with drawing_at(2):
        img = TimelinePanel([_bus_stop([("36", "Queen's Park", 2)])]).render(300, 400)

    assert img.size == (600, 800)


def test_a_timeline_with_no_stops_draws_just_the_line(drawn_text):
    TimelinePanel([]).render(300, 400)

    assert "NOW" in _texts(drawn_text)


# --- The route board ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("routes", "rows", "expected"),
    [
        ([3, 3], 6, [3, 3]),
        ([5, 5], 6, [3, 3]),
        ([5, 5], 7, [4, 3]),
        ([3, 3, 3], 7, [3, 2, 2]),
        ([2, 2], 10, [2, 2]),
        ([], 5, []),
        ([4, 4], 0, [0, 0]),
        ([4, 4], -3, [0, 0]),
    ],
)
def test_the_board_shares_its_rows_evenly_between_the_stops(routes, rows, expected):
    assert rows_each(routes, rows) == expected


@pytest.mark.parametrize(
    ("routes", "rows", "expected"),
    [
        ([1, 10], 6, [1, 5]),
        ([0, 4], 3, [0, 3]),
        ([2, 9, 1], 9, [2, 6, 1]),
        ([1, 1, 8], 8, [1, 1, 6]),
    ],
)
def test_the_rows_a_stop_with_fewer_routes_cannot_use_go_to_the_others(routes, rows, expected):
    shares = rows_each(routes, rows)

    assert shares == expected
    assert sum(shares) == min(rows, sum(routes))
    assert all(share <= count for share, count in zip(shares, routes, strict=True))


def test_the_board_leaves_out_messages():
    stop = _bus_stop([("36", "Queen's Park", 2)])

    assert RouteBoardPanel([DeparturesClient.message_panel("Needs configuring"), stop]).stops == [stop]


def test_the_board_draws_each_route_once_with_its_next_three_times(drawn_text):
    stop = _bus_stop(
        [
            *(("36", "Queen's Park", minutes) for minutes in (2, 9, 16, 23)),
            ("185", "Victoria", 0),
            ("185", "Victoria", 7),
        ]
    )

    img = RouteBoardPanel([stop]).render(500, 300)

    assert img.size == (500, 300)
    texts = _texts(drawn_text)
    assert texts.count("Queen's Park") == texts.count("Victoria") == 1
    assert {"Elephant & Castle", "NEXT", "THEN", "36", "185", "2", "9", "16", "due", "7"} <= set(texts)
    assert "23" not in texts  # only the next three


def test_a_stop_with_fewer_routes_gives_its_rows_on_the_board_to_the_other(drawn_text):
    short = _bus_stop([("36", "Alpha", 2)], "Short")
    long_destinations = ["Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]
    long = _bus_stop([(str(100 + i), to, 3 + i) for i, to in enumerate(long_destinations)], "Long")
    # Room for the two bars, the gap between them and six rows.
    height = 2 * BAR + STOP_GAP + 6 * ROW

    RouteBoardPanel([short, long]).render(500, height)

    texts = set(_texts(drawn_text))
    assert {"Short", "Long", "Alpha", *long_destinations[:5]} <= texts
    assert not texts & set(long_destinations[5:])


def test_the_board_with_no_stops_draws_nothing(drawn_text):
    img = RouteBoardPanel([]).render(300, 200)

    assert img.size == (300, 200)
    assert drawn_text == []


# --- The notice banner ----------------------------------------------------------------------------------------------

NOTICES = [
    Notice("Met Office", Severity.SEVERE, "Red warning: wind"),
    Notice("Northern", Severity.WARNING, "Severe delays"),
    Notice("Circle", Severity.INFO, "Issues Reported"),
]


def test_the_banner_is_a_line_tall_with_notices_and_nothing_without():
    assert NoticeBannerPanel(NOTICES).fitted_height(100) == NoticeBannerPanel.HEIGHT
    assert NoticeBannerPanel(NOTICES).fitted_height(10) == 10
    assert NoticeBannerPanel([]).fitted_height(100) == 0


def test_the_banner_of_a_board_has_its_notices_in_its_order():
    board = NoticeBoardPanel(NOTICES)

    assert NoticeBannerPanel.of(board).notices == board.notices


def test_the_banner_has_the_most_important_notice_and_how_many_more(drawn_text):
    img = NoticeBannerPanel(NOTICES).render(600, NoticeBannerPanel.HEIGHT)

    assert img.size == (600, NoticeBannerPanel.HEIGHT)
    texts = _texts(drawn_text)
    assert {"!", "Met Office", "Red warning: wind", "+2 more"} <= set(texts)
    assert "Severe delays" not in texts
    assert_text_inside_and_apart([call for call in drawn_text if call[0] != "!"], img.size)


def test_a_banner_with_one_notice_says_no_more(drawn_text):
    NoticeBannerPanel(NOTICES[2:]).render(600, NoticeBannerPanel.HEIGHT)

    texts = _texts(drawn_text)
    assert {"Circle", "Issues Reported"} <= set(texts)
    assert not any(text.endswith("more") for text in texts)


def test_a_banner_shows_a_notices_time_on_the_clock(drawn_text):
    until = dt.datetime(2026, 1, 15, 21, 7, tzinfo=dt.UTC)
    notice = Notice("Met Office", Severity.WARNING, "Until", until, display_time=until)

    NoticeBannerPanel([notice]).render(600, NoticeBannerPanel.HEIGHT, clock_format="12h")

    assert "Until 9:07 PM" in _texts(drawn_text)


def test_a_banner_with_no_notices_draws_nothing(drawn_text):
    img = NoticeBannerPanel([]).render(600, NoticeBannerPanel.HEIGHT)

    assert drawn_text == []
    assert img.getbbox() is None


# --- The clock panels -----------------------------------------------------------------------------------------------


def test_the_clock_and_date_are_the_renders_moment_on_the_screens_clock():
    assert clock_and_date("24h") == ("12:00", "Thursday 15 January")
    assert clock_and_date("12h") == ("12:00 PM", "Thursday 15 January")


def test_high_low_has_the_chance_of_rain_when_known():
    assert high_low(make_weather()) == "18° / 9°   Rain 40%"
    assert high_low(make_weather(precipitation_probability=None)) == "18° / 9°"


def test_the_current_page_is_now_else_the_first():
    pages = _pages()

    assert current_page(pages) is pages[0]
    assert current_page(pages[1:]) is pages[1]
    assert current_page(()) is None


def test_the_readings_and_later_rows_of_a_forecast():
    pages = _pages()

    assert [label for label, _value in readings(pages[0], "24h")] == ["High / Low", "Rain", "Sunset", "Wind"]
    assert [title.split()[0] for title, *_ in later_rows(pages, "24h")] == ["TONIGHT", "TOMORROW", "WEEKEND"]
    assert len(later_rows([make_weather(period=WeatherPeriod.NOW)], "24h")) == 1  # what it says comes next


CLOCK_PANELS = [
    (HdMastheadPanel, (700, 270)),
    (HdWeatherColumnPanel, (240, 900)),
    (HdClockPanel, (700, 96)),
]


@pytest.mark.parametrize(("panel", "size"), CLOCK_PANELS, ids=lambda value: getattr(value, "__name__", ""))
def test_a_clock_panel_draws_the_time_the_date_and_the_weather_now(panel, size, drawn_text):
    img = panel(_pages()).render(*size)

    assert img.size == size
    texts = _texts(drawn_text)
    assert {"12:00", "Thursday 15 January"} <= set(texts)
    assert any("Partly cloudy" in text for text in texts)
    assert "Could not connect" not in texts


@pytest.mark.parametrize(("panel", "size"), CLOCK_PANELS[:2], ids=lambda value: getattr(value, "__name__", ""))
def test_the_masthead_and_the_column_draw_every_later_page(panel, size, drawn_text):
    panel(_pages()).render(*size)

    texts = _texts(drawn_text)
    assert {"Slight rain", "Moderate snowfall", "Mixed skies"} <= set(texts)
    assert sum(text.startswith(("TONIGHT", "TOMORROW", "WEEKEND")) for text in texts) == 3


def test_the_masthead_draws_the_readings_under_the_weather_now(drawn_text):
    HdMastheadPanel(_pages()).render(700, 270)

    assert {"18° / 9°", "40%", "13 km/h SW"} <= set(_texts(drawn_text))


def test_the_column_draws_a_row_for_each_coming_hour_it_has_room_for(drawn_text):
    pages = _pages()
    HdWeatherColumnPanel(pages).render(240, 1400)
    tall = _texts(drawn_text)
    drawn_text.clear()
    HdWeatherColumnPanel(pages).render(240, 700)
    short = _texts(drawn_text)

    hours = [hour.time.strftime("%H:%M") for hour in pages[0].hours]
    assert hours
    assert sum(text in hours for text in short) < sum(text in hours for text in tall)
    assert hours[0] in short


@pytest.mark.parametrize(("panel", "size"), CLOCK_PANELS, ids=lambda value: getattr(value, "__name__", ""))
def test_without_a_forecast_a_clock_panel_says_why(panel, size, drawn_text):
    panel((), MESSAGE).render(*size)

    texts = _texts(drawn_text)
    assert {"12:00", "Thursday 15 January", "Weather", "Could not connect"} <= set(texts)


@pytest.mark.parametrize(("panel", "size"), CLOCK_PANELS, ids=lambda value: getattr(value, "__name__", ""))
def test_with_neither_a_forecast_nor_a_message_a_clock_panel_draws_just_the_time(panel, size, drawn_text):
    img = panel().render(*size, clock_format="12h")

    assert img.size == size
    assert _texts(drawn_text) == ["12:00 PM", "Thursday 15 January"]


@pytest.mark.parametrize(("panel", "size"), CLOCK_PANELS, ids=lambda value: getattr(value, "__name__", ""))
def test_a_clock_panel_draws_twice_as_big_at_twice_the_scale(panel, size):
    with drawing_at(2):
        img = panel(_pages()).render(*size)

    assert img.size == (2 * size[0], 2 * size[1])


def test_the_centred_header_draws_the_weather_on_a_line_under_the_time(drawn_text):
    HdClockPanel(_pages(), centred=True).render(700, 216)

    texts = _texts(drawn_text)
    assert texts[:2] == ["12:00", "Thursday 15 January"]
    assert "14°  Partly cloudy   ·   18° / 9°   Rain 40%" in texts


def test_the_centred_header_without_a_forecast_says_why_on_one_line(drawn_text):
    HdClockPanel((), MESSAGE, centred=True).render(700, 216)

    assert _texts(drawn_text) == ["12:00", "Thursday 15 January", "Weather: Could not connect"]


def test_a_header_with_a_summary_says_it_instead_of_the_weather(drawn_text):
    HdClockPanel(_pages(), summary="36 due   N in 2 min").render(700, 96)

    texts = _texts(drawn_text)
    assert "36 due   N in 2 min" in texts
    assert not any("Partly cloudy" in text for text in texts)


@pytest.mark.parametrize("page", [0, 1])
def test_the_headers_squares_fill_the_page_on_screen(page):
    width = 700
    img = HdClockPanel(squares=(page, 2)).render(width, 96)

    left = width / 2 - (2 * (SQUARE + SQUARE_GAP) - SQUARE_GAP) / 2
    middles = [round(left + i * (SQUARE + SQUARE_GAP) + SQUARE / 2) for i in range(2)]
    filled = [img.getpixel((x, 36 + SQUARE // 2))[3] == 255 for x in middles]
    assert filled == [i == page for i in range(2)]


@pytest.mark.parametrize(
    ("panel", "size"),
    [(HdMastheadPanel, (700, 270)), (HdClockPanel, (700, 96))],
    ids=lambda value: getattr(value, "__name__", ""),
)
def test_a_clock_panels_text_stays_inside_it_and_apart(panel, size, drawn_text):
    img = panel(_pages()).render(*size)

    # The weather icons are glyphs too, drawn beside the text they go with.
    assert_text_inside_and_apart([call for call in drawn_text if len(call[0]) > 1], img.size)
