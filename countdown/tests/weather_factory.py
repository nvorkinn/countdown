import datetime as dt

from countdown_core.weather.models import Upcoming, Weather


def make_weather(**overrides) -> Weather:
    fields = {
        "temperature": 14.2,
        "weather_code": 3,
        "is_day": True,
        "high": 18.4,
        "low": 9.1,
        "precipitation_probability": 40,
        "temperature_units": "°C",
        "humidity": 72,
        "humidity_units": "%",
        "wind_speed": 13.4,
        "wind_speed_units": "km/h",
        "wind_direction": 225,
        "sunrise": dt.time(7, 21),
        "sunset": dt.time(18, 4),
        "upcoming_weather_code": (Upcoming.TONIGHT, 61),
    }
    return Weather(**{**fields, **overrides})


def forecast_json(
    current: dict | None = None, daily: dict | None = None, start: dt.date = dt.date(2026, 1, 15)
) -> dict:
    """What Open-Meteo answers the forecast request with, on 15 January 2026 at 12:15, with
    `current` and `daily` replacing those values.
    """
    days = [start + dt.timedelta(days=offset) for offset in range(8)]
    temperatures = [8.0 if hour < 6 else 14.0 if hour < 18 else 9.0 for _ in days for hour in range(24)]
    return {
        "utc_offset_seconds": 0,
        "current": {
            "time": f"{start}T12:15",
            "temperature_2m": 14.2,
            "weather_code": 2,
            "is_day": 1,
            "relative_humidity_2m": 72,
            "wind_speed_10m": 13.4,
            "wind_direction_10m": 225,
            **(current or {}),
        },
        "current_units": {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"},
        "hourly": {
            "time": [f"{day}T{hour:02}:00" for day in days for hour in range(24)],
            # Today's 15:00 is 3 (overcast) and its 21:00 is 61 (rain); every other hour is clear.
            "weather_code": [{15: 3, 21: 61}.get(hour, 0) if day == start else 0 for day in days for hour in range(24)],
            "temperature_2m": temperatures,
            "apparent_temperature": [temperature - 2 for temperature in temperatures],
            "relative_humidity_2m": [80] * (24 * len(days)),
            "wind_speed_10m": [11.0] * (24 * len(days)),
            "wind_direction_10m": [225.0] * (24 * len(days)),
            "precipitation_probability": [65] * (24 * len(days)),
        },
        "daily": {
            "time": [str(day) for day in days],
            "temperature_2m_max": [18.4, 17.0, 17.0, 15.0, 16.0, 14.0, 13.0, 12.0],
            "temperature_2m_min": [9.1, 8.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0],
            "precipitation_probability_max": [40, 10, 20, 40, 30, 20, 10, 0],
            "weather_code": [61, 73, 2, 61, 3, 2, 1, 0],
            "sunrise": [f"{day}T07:{58 - index:02}" for index, day in enumerate(days)],
            "sunset": [f"{day}T16:{22 + index * 2:02}" for index, day in enumerate(days)],
            "wind_speed_10m_max": [18.0, 14.0, 16.0, 17.0, 15.0, 12.0, 11.0, 10.0],
            "wind_direction_10m_dominant": [225.0] * len(days),
            **(daily or {}),
        },
    }
