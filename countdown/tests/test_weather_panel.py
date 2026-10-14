import pytest
from weather_factory import make_weather

from countdown_core.weather.weather_panel import FALLBACK_ICON, ICON_MAP, WeatherPanel, icon_for
from countdown_core.weather.weather_trmnl_panel import HEIGHT, WeatherTrmnlPanel, _compass


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
