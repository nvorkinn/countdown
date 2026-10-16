import datetime as dt
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator


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
    temperature: list[float] = Field(alias="temperature_2m")
    apparent_temperature: list[float]
    humidity: list[int] = Field(alias="relative_humidity_2m")
    wind_speed: list[float] = Field(alias="wind_speed_10m")
    wind_direction: list[float] = Field(alias="wind_direction_10m")
    precipitation_probability: list[int | None]

    @model_validator(mode="after")
    def matching_hours(self) -> Self:
        columns = (
            self.weather_code,
            self.temperature,
            self.apparent_temperature,
            self.humidity,
            self.wind_speed,
            self.wind_direction,
            self.precipitation_probability,
        )
        if any(len(values) != len(self.time) for values in columns):
            raise ValueError("Hourly forecast columns must have one value per timestamp")
        return self


class DailyForecast(BaseModel):
    """Open-Meteo returns one parallel list per variable, one entry per day (today first)."""

    time: list[dt.date]
    temperature_max: list[float] = Field(alias="temperature_2m_max")
    temperature_min: list[float] = Field(alias="temperature_2m_min")
    precipitation_probability_max: list[int | None]
    weather_code: list[int]
    sunrise: list[dt.datetime]
    sunset: list[dt.datetime]
    wind_speed_max: list[float] = Field(alias="wind_speed_10m_max")
    wind_direction_dominant: list[float] = Field(alias="wind_direction_10m_dominant")

    @model_validator(mode="after")
    def matching_days(self) -> Self:
        columns = (
            self.temperature_max,
            self.temperature_min,
            self.precipitation_probability_max,
            self.weather_code,
            self.sunrise,
            self.sunset,
            self.wind_speed_max,
            self.wind_direction_dominant,
        )
        if any(len(values) != len(self.time) for values in columns):
            raise ValueError("Daily forecast columns must have one value per date")
        return self


class ForecastResponse(BaseModel):
    utc_offset_seconds: int
    current: CurrentConditions
    current_units: CurrentUnits
    hourly: HourlyForecast
    daily: DailyForecast


class Upcoming(StrEnum):
    """The part of the day the outlook is for: the next one that hasn't started yet."""

    THIS_AFTERNOON = "This afternoon"
    TONIGHT = "Tonight"
    TOMORROW = "Tomorrow"


class WeatherPeriod(StrEnum):
    NOW = "Now"
    TONIGHT = "Tonight"
    TOMORROW = "Tomorrow"
    WEEKEND = "Weekend"


class WeatherDay(BaseModel):
    date: dt.date
    high: float
    low: float
    weather_code: int


class Weather(BaseModel):
    """The readings for one carousel page; current conditions retain their next outlook."""

    period: WeatherPeriod = WeatherPeriod.NOW
    forecast_date: dt.date | None = None
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    feels_like: float | None = None
    days: tuple[WeatherDay, ...] = ()
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
