import datetime as dt

import pytest
from pydantic import ValidationError
from weather_factory import forecast_json

from countdown_core.weather.models import ForecastResponse, WeatherPeriod
from countdown_core.weather.weather_forecast import _coming_hours, weather_pages


def local_time(*fields: int) -> dt.datetime:
    """Make a time as Open-Meteo gives it: local to the forecast's place, with no zone."""
    return dt.datetime(*fields)  # noqa: DTZ001 -- naive on purpose, see above


def test_pages_follow_the_tab_order_and_keep_the_reported_units():
    payload = forecast_json()
    payload["current_units"] = {"temperature_2m": "°F", "relative_humidity_2m": "%", "wind_speed_10m": "mp/h"}
    pages = weather_pages(ForecastResponse.model_validate(payload))

    assert [page.period for page in pages] == list(WeatherPeriod)
    assert all(page.temperature_units == "°F" and page.wind_speed_units == "mp/h" for page in pages)
    assert pages[0].temperature == 14.2
    assert pages[2].temperature == pages[2].high == 17
    assert pages[2].low == 8
    assert pages[2].forecast_date == dt.date(2026, 1, 16)


def test_tonight_includes_the_next_morning_but_not_daytime():
    payload = forecast_json()
    hourly = payload["hourly"]
    cold = hourly["time"].index("2026-01-16T03:00")
    windy = hourly["time"].index("2026-01-16T02:00")
    daytime = hourly["time"].index("2026-01-16T06:00")
    hourly["temperature_2m"][cold] = -3
    hourly["apparent_temperature"][cold] = -6
    hourly["wind_speed_10m"][windy] = 35
    hourly["wind_direction_10m"][windy] = 90
    hourly["temperature_2m"][daytime] = -50
    tonight = weather_pages(ForecastResponse.model_validate(payload))[1]

    assert tonight.starts_at == local_time(2026, 1, 15, 18)
    assert tonight.ends_at == local_time(2026, 1, 16, 6)
    assert tonight.temperature == tonight.low == -3
    assert tonight.high == 9
    assert tonight.feels_like == -6
    assert tonight.weather_code == 61
    assert not tonight.is_day
    assert tonight.wind_speed == 35
    assert tonight.wind_direction == 90
    assert tonight.precipitation_probability == 65
    assert tonight.humidity == 80


def test_late_tonight_does_not_include_hours_that_have_already_passed():
    payload = forecast_json(current={"time": "2026-01-15T23:45"})
    evening = payload["hourly"]["time"].index("2026-01-15T18:00")
    payload["hourly"]["temperature_2m"][evening] = -40
    tonight = weather_pages(ForecastResponse.model_validate(payload))[1]

    assert tonight.temperature == 8
    assert tonight.starts_at == local_time(2026, 1, 15, 23, 45)
    assert tonight.ends_at == local_time(2026, 1, 16, 6)
    assert tonight.weather_code == 0  # the first remaining hour, not yesterday evening's rain


def test_before_dawn_tonight_means_the_coming_evening():
    payload = forecast_json(current={"time": "2026-01-15T02:00"})
    tonight = weather_pages(ForecastResponse.model_validate(payload))[1]

    assert tonight.starts_at == local_time(2026, 1, 15, 18)
    assert tonight.ends_at == local_time(2026, 1, 16, 6)


@pytest.mark.parametrize("weekday", range(7))
def test_every_weekday_has_a_complete_saturday_and_sunday(weekday):
    start = dt.date(2026, 1, 12) + dt.timedelta(days=weekday)
    weekend = weather_pages(ForecastResponse.model_validate(forecast_json(start=start)))[3]
    saturday = dt.date(2026, 1, 24) if weekday == 6 else dt.date(2026, 1, 17)

    assert weekend.forecast_date == saturday
    assert [day.date for day in weekend.days] == [saturday, saturday + dt.timedelta(days=1)]
    assert weekend.low == min(day.low for day in weekend.days)
    assert weekend.high == max(day.high for day in weekend.days)


def test_the_weekend_can_cross_a_year_boundary():
    weekend = weather_pages(ForecastResponse.model_validate(forecast_json(start=dt.date(2026, 12, 27))))[3]

    assert [day.date for day in weekend.days] == [dt.date(2027, 1, 2), dt.date(2027, 1, 3)]


def test_weekend_aggregates_the_two_days_without_using_todays_readings():
    weekend = weather_pages(ForecastResponse.model_validate(forecast_json()))[3]

    assert [day.high for day in weekend.days] == [17, 15]
    assert [day.low for day in weekend.days] == [9, 8]
    assert [day.weather_code for day in weekend.days] == [2, 61]
    assert weekend.precipitation_probability == 40
    assert weekend.wind_speed == 17


@pytest.mark.parametrize("probability", [None, 0])
def test_missing_rain_is_not_confused_with_zero_rain(probability):
    payload = forecast_json(daily={"precipitation_probability_max": [probability] * 8})
    payload["hourly"]["precipitation_probability"] = [probability] * (8 * 24)

    assert all(
        page.precipitation_probability == probability
        for page in weather_pages(ForecastResponse.model_validate(payload))
    )


def test_one_missing_rain_reading_does_not_make_a_partial_maximum_look_complete():
    payload = forecast_json()
    payload["daily"]["precipitation_probability_max"][2] = None
    payload["hourly"]["precipitation_probability"][21] = None
    pages = weather_pages(ForecastResponse.model_validate(payload))

    assert pages[0].precipitation_probability == 40
    assert pages[1].precipitation_probability is None
    assert pages[3].precipitation_probability is None


@pytest.mark.parametrize(
    ("section", "column"), [("hourly", "temperature_2m"), ("daily", "precipitation_probability_max")]
)
def test_misaligned_forecast_columns_are_rejected(section, column):
    payload = forecast_json()
    payload[section][column].pop()

    with pytest.raises(ValidationError, match="must have one value per"):
        ForecastResponse.model_validate(payload)


def test_an_incomplete_forecast_is_not_accepted_just_because_now_is_available():
    payload = forecast_json()
    for values in payload["daily"].values():
        del values[2:]

    with pytest.raises(ValueError):  # noqa: PT011 -- the point is only that it fails, not how
        weather_pages(ForecastResponse.model_validate(payload))


def test_now_carries_the_coming_hours_two_apart_for_the_hd_timeline():
    pages = weather_pages(ForecastResponse.model_validate(forecast_json()))
    now = pages[0]

    assert now.forecast_date == dt.date(2026, 1, 15)
    assert [hour.time for hour in now.hours] == [local_time(2026, 1, 15, hour) for hour in (13, 15, 17, 19, 21, 23)]
    assert [hour.temperature for hour in now.hours] == [14, 14, 14, 9, 9, 9]
    assert [hour.weather_code for hour in now.hours] == [0, 3, 0, 0, 61, 0]
    assert [hour.is_day for hour in now.hours] == [True, True, False, False, False, False]  # sunset is 16:22
    assert all(hour.precipitation_probability == 65 for hour in now.hours)
    assert all(page.hours == now.hours for page in pages[1:])  # the strip is the same under every page


def test_the_coming_hours_stop_where_the_forecast_ends():
    payload = forecast_json()
    payload["hourly"] = {column: values[:16] for column, values in payload["hourly"].items()}  # up to 15:00 today

    hours = _coming_hours(ForecastResponse.model_validate(payload))

    assert [hour.time for hour in hours] == [local_time(2026, 1, 15, 13), local_time(2026, 1, 15, 15)]
