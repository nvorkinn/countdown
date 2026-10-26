import datetime as dt
from itertools import combinations

import pytest
from PIL import ImageDraw
from weather_factory import forecast_json, make_weather

from countdown_core.weather.models import ForecastResponse, Upcoming, WeatherPeriod
from countdown_core.weather.weather_forecast import weather_pages
from countdown_core.weather.weather_panel import FALLBACK_ICON, ICON_MAP, WEATHER_CODE_DESC, WeatherPanel, icon_for
from countdown_core.weather.weather_trmnl_panel import HEIGHT, WeatherTrmnlPanel, _compass


def _assert_clear_text(calls, size):
    width, height = size
    for text, (left, top, right, bottom), _ in calls:
        assert 4 <= left < right <= width - 4, text
        assert 4 <= top < bottom <= height - 4, text
    for first, second in combinations(calls, 2):
        _, (left_a, top_a, right_a, bottom_a), _ = first
        _, (left_b, top_b, right_b, bottom_b), _ = second
        assert right_a <= left_b or right_b <= left_a or bottom_a <= top_b or bottom_b <= top_a, (
            first,
            second,
        )


@pytest.fixture
def forecast_weather():
    return {page.period: page for page in weather_pages(ForecastResponse.model_validate(forecast_json()))}


@pytest.mark.parametrize("code", [0, 1, 2])
def test_clear_and_partly_cloudy_skies_differ_between_day_and_night(code):
    assert icon_for(code, is_day=True) != icon_for(code, is_day=False)


@pytest.mark.parametrize("code", [3, 45, 63, 73, 95])
def test_other_conditions_look_the_same_day_and_night(code):
    assert icon_for(code, is_day=True) == icon_for(code, is_day=False)


def test_unmapped_code_falls_back_to_na_icon():
    assert icon_for(1234, is_day=True) == FALLBACK_ICON


def test_icon_map_covers_every_wmo_code_open_meteo_can_return():
    wmo_codes = {
        0,
        1,
        2,
        3,
        45,
        48,
        51,
        53,
        55,
        56,
        57,
        61,
        63,
        65,
        66,
        67,
        71,
        73,
        75,
        77,
        80,
        81,
        82,
        85,
        86,
        95,
        96,
        99,
    }
    assert wmo_codes == set(ICON_MAP)


def test_every_weather_icon_has_a_readable_description():
    assert set(WEATHER_CODE_DESC) == set(ICON_MAP)


@pytest.mark.parametrize("size", [(528, 280), (528, 170)])
def test_render_fills_the_requested_size_and_draws_something(size):
    img = WeatherPanel(make_weather()).render(*size)

    assert img.size == size
    assert img.getchannel("A").getbbox() is not None


def test_render_without_precipitation_probability():
    img = WeatherPanel(make_weather(precipitation_probability=None)).render(528, 280)

    assert img.size == (528, 280)


@pytest.mark.parametrize(("degrees", "expected"), [(0, "N"), (44, "NE"), (225, "SW"), (350, "N")])
def test_wind_direction_is_one_of_eight_compass_points(degrees, expected):
    assert _compass(degrees) == expected


def test_the_portrait_weather_fills_the_requested_size_and_draws_something():
    img = WeatherTrmnlPanel(make_weather()).render(470, HEIGHT)

    assert img.size == (470, HEIGHT)
    assert img.getchannel("A").getbbox() is not None


def test_a_long_wind_reading_shrinks_rather_than_runs_off_the_panel():
    img = WeatherTrmnlPanel(make_weather(wind_speed=123, wind_speed_units="mp/h")).render(470, HEIGHT)

    assert img.getchannel("A").crop((469, 0, 470, HEIGHT)).getbbox() is None


PANEL_SIZES = [
    (WeatherPanel, (528, 76)),
    (WeatherPanel, (528, 49)),
    (WeatherPanel, (528, 125)),
    (WeatherPanel, (241, 185)),
    (WeatherPanel, (528, 170)),
    (WeatherPanel, (528, 280)),
    (WeatherPanel, (241, 370)),
    (WeatherTrmnlPanel, (470, HEIGHT)),
    (WeatherTrmnlPanel, (320, 220)),
]


@pytest.mark.parametrize(("panel_type", "size"), PANEL_SIZES)
@pytest.mark.parametrize("code", [*ICON_MAP, 1234])
def test_weather_readings_have_clear_edges_and_never_overlap(panel_type, size, code, drawn_text):
    weather = make_weather(
        temperature=-123.4,
        temperature_units="°F",
        low=-130,
        high=100,
        precipitation_probability=100,
        humidity=100,
        wind_speed=123,
        wind_speed_units="mp/h",
        weather_code=code,
        upcoming_weather_code=(Upcoming.THIS_AFTERNOON, code),
    )
    img = panel_type(weather).render(*size)
    assert img.size == size
    assert set(img.getchannel("A").get_flattened_data()) == {0, 255}
    _assert_clear_text(drawn_text, size)


@pytest.mark.parametrize(
    ("panel_type", "size", "number"), [(WeatherPanel, (241, 185), "14.2"), (WeatherTrmnlPanel, (470, HEIGHT), "14")]
)
def test_temperature_keeps_the_configured_unit_visually_secondary(panel_type, size, number, drawn_text):
    panel_type(make_weather(temperature_units="°F")).render(*size)
    sizes = {text: font_size for text, _, font_size in drawn_text}

    assert sizes["°F"] < sizes[number] / 2
    assert "°C" not in sizes


def test_short_wide_panels_reflow_instead_of_miniaturizing_everything(drawn_text):
    WeatherPanel(make_weather()).render(528, 76)
    sizes = {text: font_size for text, _, font_size in drawn_text}

    assert sizes["14.2"] >= 32
    assert sizes["LOW"] >= 11
    assert sizes["40%"] >= 18


@pytest.mark.parametrize("probability", [None, 0, 100])
def test_landscape_preserves_the_daily_readings_and_optional_rain(probability, drawn_text):
    WeatherPanel(make_weather(precipitation_probability=probability)).render(241, 185)
    texts = {text for text, _, _ in drawn_text}

    assert {"NOW", "Overcast", "LOW", "9°", "HIGH", "18°"} <= texts
    if probability is None:
        assert "RAIN" not in texts
    else:
        assert {"RAIN", f"{probability}%"} <= texts


@pytest.mark.parametrize("upcoming", list(Upcoming))
def test_portrait_preserves_every_detail_and_the_next_outlook(upcoming, drawn_text):
    weather = make_weather(upcoming_weather_code=(upcoming, 61))
    WeatherTrmnlPanel(weather).render(470, HEIGHT)
    texts = {text for text, _, _ in drawn_text}

    assert {"Sunrise", "07:21", "Sunset", "18:04", "Humidity", "72%", "Wind", "13 km/h SW"} <= texts
    assert {str(upcoming).upper(), "Slight rain", icon_for(61, is_day=upcoming is not Upcoming.TONIGHT)} <= texts


@pytest.mark.parametrize("clock_format", ["12h", "24h"])
@pytest.mark.parametrize("size", [(470, HEIGHT), (320, 220)])
@pytest.mark.parametrize(
    ("period", "hour_24", "hour_12"),
    [
        (WeatherPeriod.NOW, {"00:07", "12:59"}, {"12:07 AM", "12:59 PM"}),
        (WeatherPeriod.TONIGHT, {"18:00 - 06:00"}, {"6:00 PM - 6:00 AM"}),
        (
            WeatherPeriod.TOMORROW,
            {"12:59", "Sunrise 00:07 / Sunset 12:59"},
            {"12:59 PM", "Sunrise 12:07 AM / Sunset 12:59 PM"},
        ),
    ],
)
def test_all_portrait_clocks_use_the_preference_without_overlap(  # noqa: PLR0913, PLR0917 -- pytest passes each parameter and fixture
    clock_format, size, period, hour_24, hour_12, forecast_weather, drawn_text
):
    weather = forecast_weather[period].model_copy(update={"sunrise": dt.time(0, 7), "sunset": dt.time(12, 59)})
    WeatherTrmnlPanel(weather).render(*size, clock_format=clock_format)
    expected = hour_12 if clock_format == "12h" else hour_24

    assert expected <= {text for text, _, _ in drawn_text}
    _assert_clear_text(drawn_text, size)
    for text, _, font_size in drawn_text:
        if text in expected:
            assert font_size >= (12 if size[0] == 470 else 8), text


def test_unknown_current_and_upcoming_weather_are_explicit(drawn_text):
    weather = make_weather(weather_code=1234, upcoming_weather_code=(Upcoming.TONIGHT, 1234))
    WeatherTrmnlPanel(weather).render(470, HEIGHT)
    texts = [text for text, _, _ in drawn_text]

    assert texts.count("Unknown conditions") == 2
    assert texts.count(FALLBACK_ICON) == 2


@pytest.mark.parametrize(("panel_type", "size"), PANEL_SIZES)
@pytest.mark.parametrize("period", [WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW, WeatherPeriod.WEEKEND])
@pytest.mark.parametrize("code", [0, 99, 1234])
def test_forecast_tabs_and_readings_never_overlap(panel_type, size, period, code, forecast_weather, drawn_text):  # noqa: PLR0913, PLR0917 -- pytest passes each parameter and fixture
    base = forecast_weather[period]
    weather = base.model_copy(
        update={
            "temperature": -123.4,
            "temperature_units": "°F",
            "weather_code": code,
            "low": -130,
            "high": 100,
            "feels_like": -123,
            "precipitation_probability": 100,
            "humidity": 100,
            "wind_speed": 123,
            "wind_speed_units": "mp/h",
            "days": tuple(
                day.model_copy(update={"high": 100 + index * 10, "low": -130, "weather_code": code})
                for index, day in enumerate(base.days)
            ),
        }
    )
    image = panel_type(weather).render(*size)

    assert image.size == size
    assert set(image.getchannel("A").get_flattened_data()) == {0, 255}
    _assert_clear_text(drawn_text, size)


@pytest.mark.parametrize("period", list(WeatherPeriod))
@pytest.mark.parametrize(
    ("panel_type", "size", "labels"),
    [
        (WeatherTrmnlPanel, (470, HEIGHT), ["NOW", "TONIGHT", "TOMORROW", "WEEKEND"]),
        (WeatherPanel, (242, 185), ["NOW", "NIGHT", "TMRW", "WKND"]),
        (WeatherPanel, (528, 76), ["NOW", "TONIGHT", "TOMORROW", "WEEKEND"]),
    ],
)
def test_only_the_selected_tab_has_a_filled_pill(panel_type, size, labels, period, forecast_weather, monkeypatch):  # noqa: PLR0913, PLR0917 -- pytest passes each parameter and fixture
    white_text = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, fill=None, **kwargs):
        if fill == "white":
            white_text.append(text)
        return original(draw, xy, text, fill=fill, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    panel_type(forecast_weather[period]).render(*size)

    assert white_text == [labels[list(WeatherPeriod).index(period)]]


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        (WeatherPeriod.TONIGHT, {"OVERNIGHT LOW", "8", "Feels like", "6°", "65%", "18:00 - 06:00"}),
        (WeatherPeriod.TOMORROW, {"DAYTIME HIGH", "17", "8°", "10%", "FRIDAY 16 JANUARY"}),
        (WeatherPeriod.WEEKEND, {"15–17", "Mixed skies", "SATURDAY", "SUNDAY", "17° / 9°", "15° / 8°"}),  # noqa: RUF001 -- the en dash the panel draws
    ],
)
def test_forecast_readings_match_the_selected_period(period, expected, forecast_weather, drawn_text):
    WeatherTrmnlPanel(forecast_weather[period]).render(470, HEIGHT)

    assert expected <= {text for text, _, _ in drawn_text}


@pytest.mark.parametrize(
    ("period", "condition"),
    [(WeatherPeriod.TONIGHT, "Slight rain"), (WeatherPeriod.WEEKEND, "Mixed skies")],
)
def test_short_wide_forecasts_keep_a_readable_condition(period, condition, forecast_weather, drawn_text):
    WeatherPanel(forecast_weather[period]).render(528, 76)

    assert condition in {text for text, _, _ in drawn_text}


@pytest.mark.parametrize("probability", [None, 0])
@pytest.mark.parametrize("period", [WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW, WeatherPeriod.WEEKEND])
def test_optional_rain_readings_reflow_on_forecast_pages(probability, period, forecast_weather, drawn_text):
    weather = forecast_weather[period].model_copy(update={"precipitation_probability": probability})
    WeatherTrmnlPanel(weather).render(470, HEIGHT)
    texts = {text for text, _, _ in drawn_text}

    if probability is None:
        assert "Rain" not in texts
    else:
        assert {"Rain", "0%"} <= texts
    _assert_clear_text(drawn_text, (470, HEIGHT))


def test_weekend_dates_show_both_months_when_needed(drawn_text):
    weather = weather_pages(ForecastResponse.model_validate(forecast_json(start=dt.date(2026, 1, 31))))[3]
    WeatherTrmnlPanel(weather).render(470, HEIGHT)

    assert "DAYTIME HIGHS / 31 JAN - 1 FEB" in {text for text, _, _ in drawn_text}


def test_rendering_a_page_does_not_advance_the_carousel(forecast_weather):
    panel = WeatherPanel(forecast_weather[WeatherPeriod.TONIGHT])
    frames = [panel.render(242, 185).tobytes() for _ in range(3)]

    assert frames[0] == frames[1] == frames[2]
    assert panel.weather.period is WeatherPeriod.TONIGHT


def test_an_incomplete_weekend_is_an_explicit_error(forecast_weather):
    weather = forecast_weather[WeatherPeriod.WEEKEND].model_copy(update={"days": ()})

    with pytest.raises(ValueError, match="Saturday and Sunday"):
        WeatherPanel(weather).render(242, 185)


@pytest.mark.parametrize(
    ("name", "panel_type", "size", "overrides"),
    [
        ("weather_compact", WeatherPanel, (241, 185), {}),
        ("weather_tall", WeatherPanel, (241, 370), {}),
        ("weather_no_rain", WeatherPanel, (528, 185), {"precipitation_probability": None}),
        ("weather_portrait", WeatherTrmnlPanel, (470, HEIGHT), {}),
        (
            "weather_portrait_long",
            WeatherTrmnlPanel,
            (470, HEIGHT),
            {
                "temperature": -12,
                "weather_code": 99,
                "wind_speed": 123,
                "wind_speed_units": "mp/h",
                "upcoming_weather_code": (Upcoming.THIS_AFTERNOON, 99),
            },
        ),
        (
            "weather_portrait_clear_night",
            WeatherTrmnlPanel,
            (470, HEIGHT),
            {"weather_code": 0, "is_day": False, "upcoming_weather_code": (Upcoming.TONIGHT, 0)},
        ),
    ],
)
def test_weather_layout_snapshots(snapshot, name, panel_type, size, overrides):
    snapshot.assert_matches(name, panel_type(make_weather(**overrides)).render(*size))


@pytest.mark.parametrize("period", [WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW, WeatherPeriod.WEEKEND])
@pytest.mark.parametrize(
    ("layout", "panel_type", "size"),
    [
        ("portrait", WeatherTrmnlPanel, (470, HEIGHT)),
        ("compact", WeatherPanel, (242, 185)),
    ],
)
def test_forecast_layout_snapshots(snapshot, period, layout, panel_type, size, forecast_weather):  # noqa: PLR0913, PLR0917 -- pytest passes each parameter and fixture
    image = panel_type(forecast_weather[period]).render(*size)
    snapshot.assert_matches(f"weather_carousel_{period.value.lower()}_{layout}", image)
