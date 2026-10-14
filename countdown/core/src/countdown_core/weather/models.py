import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


class GeocodingResult(BaseModel):
    latitude: float
    longitude: float
    name: str


class GeocodingResponse(BaseModel):
    # Open-Meteo leaves "results" out entirely (rather than sending []) when nothing matches
    results: list[GeocodingResult] = []


class CurrentConditions(BaseModel):
    time: dt.datetime  # the location's local time, since the forecast asks for timezone=auto
    temperature: float = Field(alias="temperature_2m")
    weather_code: int  # WMO code, see display.weather_panel.ICON_MAP
    is_day: bool
    humidity: int = Field(alias="relative_humidity_2m")
    wind_speed: float = Field(alias="wind_speed_10m")
    wind_direction: float = Field(alias="wind_direction_10m")


class CurrentUnits(BaseModel):
    temperature: str = Field(alias="temperature_2m")
    humidity: str = Field(alias="relative_humidity_2m")
    wind_speed: str = Field(alias="wind_speed_10m")


class HourlyForecast(BaseModel):
    time: list[dt.datetime]
    weather_code: list[int]


class DailyForecast(BaseModel):
    """Open-Meteo returns one parallel list per variable, one entry per day (today first)."""

    temperature_max: list[float] = Field(alias="temperature_2m_max")
    temperature_min: list[float] = Field(alias="temperature_2m_min")
    precipitation_probability_max: list[int | None]
    weather_code: list[int]
    sunrise: list[dt.datetime]
    sunset: list[dt.datetime]


class ForecastResponse(BaseModel):
    current: CurrentConditions
    current_units: CurrentUnits
    hourly: HourlyForecast
    daily: DailyForecast


class Upcoming(StrEnum):
    """The part of the day the outlook is for: the next one that hasn't started yet."""

    THIS_AFTERNOON = "This afternoon"
    TONIGHT = "Tonight"
    TOMORROW = "Tomorrow"


class Weather(BaseModel):
    """What the weather panel shows: right now, plus today's outlook."""

    temperature: float
    weather_code: int
    is_day: bool
    high: float
    low: float
    precipitation_probability: int | None = None
    temperature_units: str
    humidity: int
    humidity_units: str
    wind_speed: float
    wind_speed_units: str
    wind_direction: float  # degrees, where the wind blows from
    sunrise: dt.time
    sunset: dt.time
    # The weather (a WMO code) expected in the next part of the day.
    upcoming_weather_code: tuple[Upcoming, int]
