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


def forecast_json(current: dict | None = None, daily: dict | None = None) -> dict:
    """What Open-Meteo answers the forecast request with, on 15 January 2026 at 12:15, with
    `current` and `daily` replacing those values."""
    days = (15, 16)
    return {
        "current": {
            "time": "2026-01-15T12:15",
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
            "time": [f"2026-01-{day}T{hour:02}:00" for day in days for hour in range(24)],
            # Today's 15:00 is 3 (overcast) and its 21:00 is 61 (rain); every other hour is clear.
            "weather_code": [{15: 3, 21: 61}.get(hour, 0) if day == 15 else 0 for day in days for hour in range(24)],
        },
        "daily": {
            "time": [f"2026-01-{day}" for day in days],
            "temperature_2m_max": [18.4, 17.0],
            "temperature_2m_min": [9.1, 8.0],
            "precipitation_probability_max": [40, 10],
            "weather_code": [61, 73],
            "sunrise": ["2026-01-15T07:58", "2026-01-16T07:57"],
            "sunset": ["2026-01-15T16:22", "2026-01-16T16:24"],
            **(daily or {}),
        },
    }
