import datetime as dt

from countdown_core.weather.models import ForecastResponse, Upcoming, Weather, WeatherDay, WeatherPeriod


def weather_pages(forecast: ForecastResponse) -> tuple[Weather, ...]:
    """Build all four pages before accepting a forecast into the client's cache."""
    current, daily, hourly = forecast.current, forecast.daily, forecast.hourly
    today = current.time.date()
    today_index = daily.time.index(today)
    tomorrow = today + dt.timedelta(days=1)
    tomorrow_index = daily.time.index(tomorrow)
    now = Weather(
        temperature=current.temperature,
        weather_code=current.weather_code,
        is_day=current.is_day,
        high=daily.temperature_max[today_index],
        low=daily.temperature_min[today_index],
        precipitation_probability=daily.precipitation_probability_max[today_index],
        temperature_units=forecast.current_units.temperature,
        humidity=current.humidity,
        humidity_units=forecast.current_units.humidity,
        wind_speed=current.wind_speed,
        wind_speed_units=forecast.current_units.wind_speed,
        wind_direction=current.wind_direction,
        sunrise=daily.sunrise[today_index].time(),
        sunset=daily.sunset[today_index].time(),
        upcoming_weather_code=_upcoming(forecast),
    )

    start = max(current.time, dt.datetime.combine(today, dt.time(18)))
    end = dt.datetime.combine(tomorrow, dt.time(6))
    hours = [index for index, time in enumerate(hourly.time) if start <= time < end]
    if not hours:
        msg = "Forecast has no hourly readings for tonight"
        raise ValueError(msg)
    coldest = min(hours, key=lambda index: hourly.temperature[index])
    windiest = max(hours, key=lambda index: hourly.wind_speed[index])
    representative = next((index for index in hours if hourly.time[index].hour == 21), hours[0])
    tonight = now.model_copy(
        update={
            "period": WeatherPeriod.TONIGHT,
            "forecast_date": today,
            "starts_at": start,
            "ends_at": end,
            "temperature": hourly.temperature[coldest],
            "feels_like": hourly.apparent_temperature[coldest],
            "weather_code": hourly.weather_code[representative],
            "is_day": False,
            "low": hourly.temperature[coldest],
            "high": max(hourly.temperature[index] for index in hours),
            "precipitation_probability": _rain_probability(
                [hourly.precipitation_probability[index] for index in hours]
            ),
            "humidity": round(sum(hourly.humidity[index] for index in hours) / len(hours)),
            "wind_speed": hourly.wind_speed[windiest],
            "wind_direction": hourly.wind_direction[windiest],
            "sunrise": daily.sunrise[tomorrow_index].time(),
        }
    )

    noon = hourly.time.index(dt.datetime.combine(tomorrow, dt.time(12)))
    tomorrow_weather = now.model_copy(
        update={
            "period": WeatherPeriod.TOMORROW,
            "forecast_date": tomorrow,
            "temperature": daily.temperature_max[tomorrow_index],
            "weather_code": daily.weather_code[tomorrow_index],
            "is_day": True,
            "high": daily.temperature_max[tomorrow_index],
            "low": daily.temperature_min[tomorrow_index],
            "precipitation_probability": daily.precipitation_probability_max[tomorrow_index],
            "humidity": hourly.humidity[noon],
            "wind_speed": daily.wind_speed_max[tomorrow_index],
            "wind_direction": daily.wind_direction_dominant[tomorrow_index],
            "sunrise": daily.sunrise[tomorrow_index].time(),
            "sunset": daily.sunset[tomorrow_index].time(),
        }
    )

    # Saturday includes the current weekend; on Sunday, show the next complete weekend.
    saturday = today + dt.timedelta(days=(5 - today.weekday()) % 7)
    weekend_indices = [daily.time.index(saturday + dt.timedelta(days=offset)) for offset in (0, 1)]
    days = tuple(
        WeatherDay(
            date=daily.time[index],
            high=daily.temperature_max[index],
            low=daily.temperature_min[index],
            weather_code=daily.weather_code[index],
        )
        for index in weekend_indices
    )
    weekend_wind = max(weekend_indices, key=lambda index: daily.wind_speed_max[index])
    weekend_hours = [index for index, time in enumerate(hourly.time) if saturday <= time.date() <= days[-1].date]
    if not weekend_hours:
        msg = "Forecast has no hourly readings for the weekend"
        raise ValueError(msg)
    weekend = now.model_copy(
        update={
            "period": WeatherPeriod.WEEKEND,
            "forecast_date": saturday,
            "days": days,
            "temperature": max(day.high for day in days),
            "weather_code": days[0].weather_code,
            "is_day": True,
            "high": max(day.high for day in days),
            "low": min(day.low for day in days),
            "precipitation_probability": _rain_probability(
                [daily.precipitation_probability_max[index] for index in weekend_indices]
            ),
            "humidity": round(sum(hourly.humidity[index] for index in weekend_hours) / len(weekend_hours)),
            "wind_speed": daily.wind_speed_max[weekend_wind],
            "wind_direction": daily.wind_direction_dominant[weekend_wind],
            "sunrise": daily.sunrise[weekend_indices[0]].time(),
            "sunset": daily.sunset[weekend_indices[-1]].time(),
        }
    )
    return now, tonight, tomorrow_weather, weekend


def _rain_probability(values: list[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return max(value for value in values if value is not None)


def _upcoming(forecast: ForecastResponse) -> tuple[Upcoming, int]:
    now = forecast.current.time
    for part, starts, hour in ((Upcoming.THIS_AFTERNOON, 12, 15), (Upcoming.TONIGHT, 18, 21)):
        if now.hour < starts:
            at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            return part, forecast.hourly.weather_code[forecast.hourly.time.index(at)]
    tomorrow = forecast.daily.time.index(now.date() + dt.timedelta(days=1))
    return Upcoming.TOMORROW, forecast.daily.weather_code[tomorrow]
