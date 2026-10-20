"""Open-Meteo's responses, and the readings each page of the weather panel draws."""

import datetime as dt
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from countdown_core.i18n import N_


class OpenMeteoModel(BaseModel):
    """A model of Open-Meteo's JSON, which may use Open-Meteo's names (aliases) or our own field names.

    Open-Meteo sends its names ("temperature_2m"); the weather provider stores the parsed forecast in Redis with
    model_dump_json(), which writes ours ("temperature"). Accepting both lets a renderer read that back.
    """

    model_config = ConfigDict(validate_by_alias=True, validate_by_name=True)


class GeocodingResult(BaseModel):
    """A place Open-Meteo's geocoder matched to the configured location."""

    latitude: float
    longitude: float
    name: str


class GeocodingResponse(BaseModel):
    """The geocoder's matches, best first."""

    # Open-Meteo leaves "results" out entirely (rather than sending []) when nothing matches
    results: list[GeocodingResult] = []


class CurrentConditions(OpenMeteoModel):
    """The weather now."""

    time: dt.datetime  # the location's local time, since the forecast asks for timezone=auto
    temperature: float = Field(alias="temperature_2m")
    weather_code: int  # WMO code, see weather_panel.ICON_MAP
    is_day: bool
    humidity: int = Field(alias="relative_humidity_2m")
    wind_speed: float = Field(alias="wind_speed_10m")
    wind_direction: float = Field(alias="wind_direction_10m")


class CurrentUnits(OpenMeteoModel):
    """The units the current conditions are in, such as "°C"."""

    temperature: str = Field(alias="temperature_2m")
    humidity: str = Field(alias="relative_humidity_2m")
    wind_speed: str = Field(alias="wind_speed_10m")


class HourlyForecast(OpenMeteoModel):
    """One parallel list per variable, one entry per hour."""

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
        """Check every column has one value per hour."""
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
            msg = "Hourly forecast columns must have one value per timestamp"
            raise ValueError(msg)
        return self


class DailyForecast(OpenMeteoModel):
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
        """Check every column has one value per day."""
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
            msg = "Daily forecast columns must have one value per date"
            raise ValueError(msg)
        return self


class ForecastResponse(OpenMeteoModel):
    """Open-Meteo's forecast for one place, in its local time."""

    utc_offset_seconds: int
    current: CurrentConditions
    current_units: CurrentUnits
    hourly: HourlyForecast
    daily: DailyForecast


# The values are English, and translated where they're drawn (see i18n): N_ marks them for that.
class Upcoming(StrEnum):
    """The part of the day the outlook is for: the next one that hasn't started yet."""

    THIS_AFTERNOON = N_("This afternoon")
    TONIGHT = N_("Tonight")
    TOMORROW = N_("Tomorrow")


class WeatherPeriod(StrEnum):
    """The pages of the weather carousel, by the tab each is under."""

    NOW = N_("Now")
    TONIGHT = N_("Tonight")
    TOMORROW = N_("Tomorrow")
    WEEKEND = N_("Weekend")


class WeatherDay(BaseModel):
    """One day of the weekend page."""

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
