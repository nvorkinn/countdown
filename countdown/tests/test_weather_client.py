"""The weather client: the forecast the weather provider stored for the configured location, a page at a time."""

import asyncio
import datetime as dt
import json

import pytest
from test_utils import REGISTRATION
from weather_factory import forecast_json

from countdown_api.models import Weather as WeatherSection
from countdown_core.store import keys, reader
from countdown_core.weather import weather_client
from countdown_core.weather.models import Upcoming, Weather, WeatherPeriod
from countdown_core.weather.weather_client import NoStoredForecastError, WeatherClient, location_key

FORECAST_JSON = forecast_json()
LONDON = "london-gb-2643743"


def _forecast_at(time: str) -> dict:
    return {**FORECAST_JSON, "current": {**FORECAST_JSON["current"], "time": time}}


def weather_section(location: str = "London", key: str | None = None) -> WeatherSection:
    """Return the broker's weather section; with no key, as a broker that predates it sends it."""
    return WeatherSection(location=location, key=key)


def make_client(location: str = "London", key: str | None = None) -> WeatherClient:
    return WeatherClient(REGISTRATION, weather_section(location, key))


def put_forecast(store, forecast: dict = FORECAST_JSON, location: str = "London") -> None:
    """Store `forecast` (Open-Meteo's JSON, as is, so a broken one can be stored too) as the weather provider does."""
    stored = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "location": location, "forecast": forecast}
    store.set(keys.weather(location), json.dumps(stored))


@pytest.fixture
def reads(store, monkeypatch) -> list[str]:
    """Record the key of every read of the store."""
    seen: list[str] = []
    get = store.get

    def recording_get(key):
        seen.append(key)
        return get(key)

    monkeypatch.setattr(store, "get", recording_get)
    return seen


def test_update_reads_the_locations_forecast_from_the_store(store, reads):
    put_forecast(store)

    panel = asyncio.run(make_client().update())

    assert panel.weather == Weather(
        temperature=14.2,
        weather_code=2,
        is_day=True,
        high=18.4,
        low=9.1,
        precipitation_probability=40,
        temperature_units="°C",
        humidity=72,
        humidity_units="%",
        wind_speed=13.4,
        wind_speed_units="km/h",
        wind_direction=225,
        sunrise=dt.time(7, 58),
        sunset=dt.time(16, 22),
        upcoming_weather_code=(Upcoming.TONIGHT, 61),
    )
    assert reads == [keys.weather("London")]


def test_update_reads_the_forecast_under_the_places_key(store, reads):
    """The broker subscribes the picked place's key, so screens that typed "London" and "london" share a forecast."""
    put_forecast(store, location=LONDON)

    assert asyncio.run(make_client(key=LONDON).update()).weather.temperature == 14.2
    assert reads == [keys.weather(LONDON)]


def test_until_the_places_key_is_in_the_store_it_reads_the_one_under_the_location(store, reads):
    """Just after the broker starts sending keys: the provider hasn't fetched the key's, the old one hasn't expired."""
    put_forecast(store, location="London")

    assert asyncio.run(make_client(key=LONDON).update()).weather.temperature == 14.2
    assert reads == [keys.weather(LONDON), keys.weather("London")]


def test_the_forecast_under_the_places_key_wins_over_the_one_under_the_location(store, reads):
    put_forecast(store, location="London")
    put_forecast(store, forecast_json(current={"temperature_2m": -3.0}), location=LONDON)

    assert asyncio.run(make_client(key=LONDON).update()).weather.temperature == -3.0
    assert reads == [keys.weather(LONDON)]


def test_neither_in_the_store_raises_so_the_last_panel_stays(reads):
    with pytest.raises(NoStoredForecastError, match=r"^No forecast in the store for the screen's place$"):
        asyncio.run(make_client(key=LONDON).update())  # not naming the key: the registry logs this
    assert reads == [keys.weather(LONDON), keys.weather("London")]


def test_a_key_that_is_the_location_is_read_once(reads):
    with pytest.raises(NoStoredForecastError):
        asyncio.run(make_client(location="london", key="london").update())
    assert reads == [keys.weather("london")]


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        (weather_section("London", LONDON), LONDON),
        (weather_section("London"), "London"),  # an older broker sends null: it subscribes the location
        (weather_section(""), ""),
    ],
)
def test_the_location_key_is_the_brokers_key_or_else_its_location(section, expected):
    assert location_key(section) == expected


def test_without_a_store_it_cant_be_built_and_says_why(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)

    with pytest.raises(reader.StoreNotConfiguredError, match="REDIS_URL"):
        make_client()


@pytest.mark.parametrize(
    ("time", "upcoming"),
    [
        ("2026-01-15T08:30", (Upcoming.THIS_AFTERNOON, 3)),  # today at 15:00
        ("2026-01-15T12:00", (Upcoming.TONIGHT, 61)),  # today at 21:00
        ("2026-01-15T18:00", (Upcoming.TOMORROW, 73)),  # tomorrow's daily code
        ("2026-01-15T23:45", (Upcoming.TOMORROW, 73)),
    ],
)
def test_the_outlook_is_for_the_next_part_of_the_day(store, time, upcoming):
    put_forecast(store, _forecast_at(time))

    assert asyncio.run(make_client().update()).weather.upcoming_weather_code == upcoming


def test_forecast_can_lack_a_precipitation_probability(store):
    put_forecast(
        store, {**FORECAST_JSON, "daily": {**FORECAST_JSON["daily"], "precipitation_probability_max": [None] * 8}}
    )

    assert asyncio.run(make_client().update()).weather.precipitation_probability is None


def test_no_location_configured_says_it_needs_configuring_without_reading_the_store(reads):
    """The broker subscribes no location for it, so there's nothing in the store to read."""
    assert asyncio.run(make_client(location="").update()).message == "Needs configuring"
    assert reads == []


def test_a_forecast_not_in_the_store_raises_so_the_last_panel_stays():
    with pytest.raises(NoStoredForecastError):
        asyncio.run(make_client().update())


def test_each_minute_shows_the_next_tab_without_rereading_the_forecast(store, reads, rotation_clock):
    put_forecast(store)
    client = make_client()

    panels = []
    for minute in range(6):
        rotation_clock.at_minute(minute)
        panels.append(asyncio.run(client.update()))

    assert [panel.weather.period for panel in panels] == [
        WeatherPeriod.NOW,
        WeatherPeriod.TONIGHT,
        WeatherPeriod.TOMORROW,
        WeatherPeriod.WEEKEND,
        WeatherPeriod.NOW,
        WeatherPeriod.TONIGHT,
    ]
    assert len({id(panel) for panel in panels}) == len(panels)
    assert len(reads) == 1


def test_rereads_after_refresh_interval(store, reads, rotation_clock):
    put_forecast(store)
    client = make_client()
    asyncio.run(client.update())
    client._last_attempt -= weather_client.REFRESH_INTERVAL + dt.timedelta(seconds=1)
    rotation_clock.advance()

    panel = asyncio.run(client.update())

    assert panel.weather.period is WeatherPeriod.TONIGHT
    assert len(reads) == 2


@pytest.mark.parametrize(
    ("offset_hours", "local_time", "refresh"),
    [(-8, "23:59", True), (0, "23:59", True), (14, "23:59", True), (-8, "15:59", False), (14, "13:59", False)],
)
def test_date_rollover_uses_the_weather_locations_date_without_resetting_the_carousel(  # noqa: PLR0913, PLR0917 -- three parameters and four fixtures
    store, reads, offset_hours, local_time, refresh, monkeypatch, rotation_clock
):
    first_forecast = forecast_json(current={"time": f"2026-01-15T{local_time}"})
    next_forecast = forecast_json(current={"time": "2026-01-16T00:01"}, start=dt.date(2026, 1, 16))
    first_forecast["utc_offset_seconds"] = next_forecast["utc_offset_seconds"] = offset_hours * 3600
    put_forecast(store, first_forecast)
    clock = [
        dt.datetime.fromisoformat(f"2026-01-15T{local_time}").replace(tzinfo=dt.UTC) - dt.timedelta(hours=offset_hours)
    ]

    class FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(weather_client.dt, "datetime", FrozenDatetime)
    client = make_client()
    first = asyncio.run(client.update())
    put_forecast(store, next_forecast)
    clock[0] += dt.timedelta(minutes=2)
    rotation_clock.advance()

    second = asyncio.run(client.update())

    assert first.weather.period is WeatherPeriod.NOW
    assert second.weather.period is WeatherPeriod.TONIGHT
    assert second.weather.forecast_date == dt.date(2026, 1, 16 if refresh else 15)
    assert len(reads) == (2 if refresh else 1)


@pytest.mark.parametrize("failure", ["missing", "redis", "misaligned", "incomplete"])
def test_failed_refresh_keeps_the_last_good_frame(store, failure, monkeypatch, rotation_clock):
    put_forecast(store)
    client = make_client()
    first = asyncio.run(client.update())
    client._last_attempt -= weather_client.REFRESH_INTERVAL
    rotation_clock.advance()
    if failure == "missing":
        store.delete(keys.weather("London"))
        expected = NoStoredForecastError
    elif failure == "redis":
        get = store.get

        def unreachable(key):
            monkeypatch.setattr(store, "get", get)  # just this once: Redis is back for the next update
            raise reader.redis.exceptions.ConnectionError

        monkeypatch.setattr(store, "get", unreachable)
        expected = reader.redis.exceptions.ConnectionError
    else:
        broken = forecast_json()
        if failure == "misaligned":
            broken["daily"]["temperature_2m_max"].pop()
        else:
            for values in broken["daily"].values():
                del values[2:]
        put_forecast(store, broken)
        expected = ValueError

    with pytest.raises(expected):
        asyncio.run(client.update())

    assert client._panel is first
    put_forecast(store)
    assert asyncio.run(client.update()).weather.period is WeatherPeriod.TONIGHT  # the clock's tab, this minute


def test_refreshed_forecast_data_is_used_on_the_next_tab(store, rotation_clock):
    put_forecast(store)
    client = make_client()
    asyncio.run(client.update())
    refreshed = forecast_json()
    refreshed["hourly"]["precipitation_probability"] = [35] * (8 * 24)
    put_forecast(store, refreshed)
    client._last_attempt -= weather_client.REFRESH_INTERVAL
    rotation_clock.advance()

    tonight = asyncio.run(client.update()).weather

    assert tonight.period is WeatherPeriod.TONIGHT
    assert tonight.precipitation_probability == 35


def test_a_forecast_missing_at_first_is_read_on_the_next_cycle(store):
    client = make_client()

    with pytest.raises(NoStoredForecastError):
        asyncio.run(client.update())
    put_forecast(store)

    assert asyncio.run(client.update()).weather.temperature == 14.2


def test_needs_refresh_only_when_the_location_changes():
    client = WeatherClient(REGISTRATION, WeatherSection(location="London"))

    assert not client.needs_refresh(WeatherSection(location="London"))
    assert client.needs_refresh(WeatherSection(location="Paris"))


def test_needs_refresh_when_the_key_changes_or_the_broker_starts_sending_one():
    client = make_client(key=LONDON)

    assert not client.needs_refresh(weather_section("London", LONDON))
    assert client.needs_refresh(weather_section("London", "london-ca-6058560"))  # London, Ontario
    assert client.needs_refresh(weather_section("London"))
    assert make_client().needs_refresh(weather_section("London", LONDON))
