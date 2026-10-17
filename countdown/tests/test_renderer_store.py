"""The renderer reading the store: TfL departures and the weather from Redis (fakeredis), and only when configured."""

import asyncio
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
import responses
from test_departures_client import make_config
from test_tfl_provider import BUS_STOP, NOW, TUBE_STATION, bus, train
from test_utils import REGISTRATION
from weather_factory import forecast_json

from countdown_api.models import Weather as WeatherSection
from countdown_core.departures.departures_client import DeparturesClient
from countdown_core.store import keys, reader
from countdown_core.store.models import StoredForecast
from countdown_core.tfl.bus_arrival_panel import BusArrivalPanel
from countdown_core.tfl.stored_source import NoStoredDeparturesError, StoredTflSource, stored_panel
from countdown_core.tfl.tfl_client import TflSource
from countdown_core.tfl.tube_arrival_panel import TubeArrivalPanel
from countdown_core.weather.models import ForecastResponse
from countdown_core.weather.weather_client import NoStoredForecastError, WeatherClient
from countdown_core.weather.weather_forecast import weather_pages
from countdown_providers.tfl.main import to_stop_departures


@pytest.fixture
def store(monkeypatch):
    """Return a fake Redis that this renderer reads, as if it had been given REDIS_URL."""
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: client)
    return client


@pytest.fixture
def no_store(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)


# --- Which source -------------------------------------------------------------------


def test_reads_no_store_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    reader.store.cache_clear()
    try:
        assert reader.store() is None
    finally:
        reader.store.cache_clear()


def test_reads_the_store_given_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")
    reader.store.cache_clear()
    try:
        client = reader.store()
        assert client is not None
        assert client.connection_pool.connection_kwargs["password"] == "secret"
    finally:
        reader.store.cache_clear()


def test_departures_come_from_tfl_without_a_store(no_store):
    client = DeparturesClient(REGISTRATION, make_config(["490000123W"]))
    assert isinstance(client.sources["tfl"], TflSource)


def test_departures_come_from_the_store_with_one(store):
    client = DeparturesClient(REGISTRATION, make_config(["490000123W"]))
    assert isinstance(client.sources["tfl"], StoredTflSource)


# --- TfL from the store -------------------------------------------------------------


def test_a_stored_bus_stop_draws_as_tfls_own_would():
    arrivals = [bus("12", "Oxford Circus", 300), bus("12", "Oxford Circus", 60), bus("148", "Camberwell", 120)]
    stored = to_stop_departures(BUS_STOP, arrivals, NOW)
    from_store = stored_panel(stored, "hero", NOW)
    from_tfl = BusArrivalPanel(BUS_STOP, arrivals, "hero")
    assert from_store.departures == from_tfl.departures
    assert (from_store.stop_name, from_store.stop_letter, from_store.is_tube) == (
        from_tfl.stop_name,
        from_tfl.stop_letter,
        from_tfl.is_tube,
    )


def test_a_stored_tube_station_draws_as_tfls_own_would():
    arrivals = [
        train("Morden", 240),
        train("Edgware via Charing Cross", 90),
        train("Morden", 30),
        train("Terminates here", 10, destination_naptan_id=TUBE_STATION.naptan_id),
    ]
    stored = to_stop_departures(TUBE_STATION, arrivals, NOW)
    from_store = stored_panel(stored, "hero", NOW)
    from_tfl = TubeArrivalPanel(TUBE_STATION, arrivals, "hero")
    assert from_store.departures == from_tfl.departures
    assert (from_store.stop_name, from_store.is_tube) == (from_tfl.stop_name, from_tfl.is_tube) == ("Kennington", True)


def test_stored_departures_count_down_from_when_the_panel_is_made():
    stored = to_stop_departures(BUS_STOP, [bus("12", "Oxford Circus", 600)], NOW)
    later = stored_panel(stored, "hero", NOW + timedelta(minutes=4))
    assert later.departures[0].minutes == (6,)


def test_the_stored_source_reads_the_stops_key(store):
    store.set(
        keys.departures(BUS_STOP.naptan_id), to_stop_departures(BUS_STOP, [bus("12", "X", 60)], NOW).model_dump_json()
    )
    source = StoredTflSource(store, now=lambda: NOW)
    stop = source.resolve(BUS_STOP.naptan_id)
    assert source.panel(stop, "hero").stop_name == BUS_STOP.common_name


def test_a_stop_not_in_the_store_yet_still_resolves_but_raises_when_drawn(store):
    source = StoredTflSource(store)
    stop = source.resolve("490000999Z")
    assert stop == "490000999Z"  # kept, so it's drawn once the provider has fetched it
    with pytest.raises(NoStoredDeparturesError):
        source.panel(stop, "hero")


def test_departures_client_draws_stored_stops_and_skips_missing_ones(store):
    store.set(
        keys.departures(BUS_STOP.naptan_id), to_stop_departures(BUS_STOP, [bus("12", "X", 60)], NOW).model_dump_json()
    )
    client = DeparturesClient(REGISTRATION, make_config([BUS_STOP.naptan_id, "490000999Z"]))
    asyncio.run(client.initialise())
    panel = client._update()
    assert [p.stop_name for p in panel.arrival_panels] == [BUS_STOP.common_name]


def test_departures_client_raises_when_nothing_is_in_the_store_so_the_last_panel_stays(store):
    client = DeparturesClient(REGISTRATION, make_config(["490000999Z"]))
    asyncio.run(client.initialise())
    with pytest.raises(RuntimeError):
        client._update()


# --- Weather from the store ---------------------------------------------------------


def make_weather_client(location: str = "London") -> WeatherClient:
    return WeatherClient(REGISTRATION, WeatherSection(api_key="", location=location))


@responses.activate  # with nothing registered: any call to Open-Meteo fails the test
def test_weather_comes_from_the_store_without_asking_open_meteo(store):
    forecast = ForecastResponse.model_validate(forecast_json())
    stored = StoredForecast(fetched_at=datetime.now(UTC), location="London", forecast=forecast)
    store.set(keys.weather("London"), stored.model_dump_json())
    client = make_weather_client("London")
    panel = client._update()
    assert panel.weather == weather_pages(forecast)[0]
    assert len(responses.calls) == 0


def test_weather_not_in_the_store_raises_so_the_last_panel_stays(store):
    with pytest.raises(NoStoredForecastError):
        make_weather_client("London")._update()


@responses.activate
def test_weather_without_a_location_still_asks_open_meteo_for_the_north_pole(store):
    from countdown_core.weather.weather_client import FORECAST_URL  # noqa: PLC0415

    responses.add(responses.GET, FORECAST_URL, json=forecast_json())
    make_weather_client("")._update()
    assert len(responses.calls) == 1
