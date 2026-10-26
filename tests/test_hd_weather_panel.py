import pytest
from test_utils import assert_text_inside_and_apart
from weather_factory import forecast_json, make_weather

from countdown_renderer.rendering.layouts.hd_portrait import ARRIVALS_SCALE, HD_PORTRAIT, SCALE, WEATHER_HEIGHT
from countdown_renderer.utils.scaled_draw import drawing_at
from countdown_renderer.utils.utils import GAP
from countdown_renderer.weather.hd_weather_panel import DESIGNS, HdWeatherPanel
from countdown_renderer.weather.weather_forecast import weather_pages
from countdown_shared.weather.models import WEATHER_DESIGNS, ForecastResponse, WeatherPeriod

# The room the HD portrait layout gives the weather, in the panel's own pixels, and at the stops' scale.
SIZE = (int((HD_PORTRAIT.width - 2 * SCALE * GAP) / ARRIVALS_SCALE), int(WEATHER_HEIGHT / ARRIVALS_SCALE))
DRAWN_SIZE = (round(SIZE[0] * ARRIVALS_SCALE), round(SIZE[1] * ARRIVALS_SCALE))

PAGES = weather_pages(ForecastResponse.model_validate(forecast_json()))
PERIODS = [page.period.value.lower() for page in PAGES[1:]]


def _render(panel: HdWeatherPanel, clock_format="24h"):
    with drawing_at(ARRIVALS_SCALE):
        return panel.render(*SIZE, clock_format=clock_format)


def test_every_design_in_the_contract_is_drawn():
    assert set(DESIGNS) == set(WEATHER_DESIGNS)


def test_without_the_other_pages_the_panel_has_only_its_own():
    assert HdWeatherPanel(PAGES[1], "overview").pages == (PAGES[1],)


@pytest.mark.parametrize("design", WEATHER_DESIGNS)
@pytest.mark.parametrize(
    "page", [*PAGES, make_weather(precipitation_probability=None)], ids=["now", *PERIODS, "no rain"]
)
@pytest.mark.parametrize("clock_format", ["12h", "24h"])
def test_each_design_fills_the_hd_weathers_room_with_no_text_cut_off_or_on_top_of_other_text(
    design, page, clock_format, drawn_text
):
    img = _render(HdWeatherPanel(page, design, PAGES), clock_format)

    assert img.size == DRAWN_SIZE
    assert drawn_text, "nothing was drawn"
    assert_text_inside_and_apart(drawn_text, img.size)


def test_the_overview_draws_every_page_at_once(drawn_text):
    _render(HdWeatherPanel(PAGES[0], "overview", PAGES))

    texts = [text for text, _box, _size in drawn_text]
    assert all(period.value.upper() in texts for period in WeatherPeriod)
    assert "18:00 - 06:00" in texts  # tonight's hours
    assert "FRIDAY 16 JANUARY" in texts  # tomorrow's date
    assert texts.count("SATURDAY") == texts.count("SUNDAY") == 1  # the weekend's days


def test_the_timeline_draws_the_coming_hours_with_their_temperature_and_rain(drawn_text):
    _render(HdWeatherPanel(PAGES[0], "timeline", PAGES), "12h")

    texts = [text for text, _box, _size in drawn_text]
    assert {"1:00 PM", "3:00 PM", "5:00 PM", "7:00 PM", "9:00 PM", "11:00 PM"} <= set(texts)
    assert texts.count("14°") == 3
    assert texts.count("9°") == 3
    assert texts.count("65% rain") == 6


def test_the_timeline_strip_is_empty_for_a_page_without_hours(drawn_text):
    _render(HdWeatherPanel(make_weather(), "timeline"))

    assert not any(text.endswith("% rain") for text, _box, _size in drawn_text)
