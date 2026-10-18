"""The renderer reading the store: TfL and Entur departures and the weather from Redis (fakeredis)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import requests
import responses
from test_departures_client import make_config
from test_entur_provider import MAJORSTUEN_ID, QUAY_ID, fetched, mock_entur
from test_tfl_provider import BUS_STOP, NOW, TUBE_STATION, bus, train
from test_utils import REGISTRATION
from weather_factory import forecast_json

from countdown_api.models import DepartureStop
from countdown_api.models import Weather as WeatherSection
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.entur.entur_arrival_panel import EnturArrivalPanel
from countdown_core.entur.entur_source import EnturSource
from countdown_core.entur.stored_source import StoredEnturPanel, StoredEnturSource
from countdown_core.store import keys, reader
from countdown_core.store.models import StoredForecast
from countdown_core.tfl.abstract_arrival_panel import Departures
from countdown_core.tfl.stored_source import NoStoredDeparturesError, StoredTflSource, stored_panel
from countdown_core.weather.models import ForecastResponse
from countdown_core.weather.weather_client import NoStoredForecastError, WeatherClient
from countdown_core.weather.weather_forecast import weather_pages
from countdown_providers.entur.main import to_stop_departures as entur_stop_departures
from countdown_providers.tfl.main import to_stop_departures

# The real reader.store and reader.binary_store, which conftest's `store` fixture replaces in every test.
REAL_STORE = reader.store
REAL_BINARY_STORE = reader.binary_store


@pytest.fixture
def real_store(monkeypatch):
    """Use the real reader.store, reading REDIS_URL, afresh."""
    monkeypatch.setattr(reader, "store", REAL_STORE)
    REAL_STORE.cache_clear()
    yield REAL_STORE
    REAL_STORE.cache_clear()


# --- Which source -------------------------------------------------------------------


def test_reads_no_store_without_redis_url(monkeypatch, real_store):
    monkeypatch.delenv("REDIS_URL", raising=False)

    assert reader.store() is None


def test_requiring_a_store_without_redis_url_says_what_is_missing(monkeypatch, real_store):
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(reader.StoreNotConfiguredError, match="REDIS_URL is not set"):
        reader.require_store()


def test_reads_the_store_given_redis_url(monkeypatch, real_store):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")

    client = reader.require_store()

    assert client is reader.store()
    assert client.connection_pool.connection_kwargs["password"] == "secret"


def test_the_covers_are_read_from_the_same_store_as_bytes(monkeypatch, real_store):
    """The covers (keys.album_art) are image files, which the store's own client would decode as text."""
    monkeypatch.setattr(reader, "binary_store", REAL_BINARY_STORE)
    REAL_BINARY_STORE.cache_clear()
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")
    try:
        binary, text = reader.binary_store(), reader.require_store()

        assert binary is reader.binary_store()
        assert binary is not text
        assert binary.connection_pool.connection_kwargs["decode_responses"] is False
        assert text.connection_pool.connection_kwargs["decode_responses"] is True
        for key in ("host", "port", "db", "password", "socket_timeout"):
            assert binary.connection_pool.connection_kwargs[key] == text.connection_pool.connection_kwargs[key]

        monkeypatch.delenv("REDIS_URL")
        REAL_BINARY_STORE.cache_clear()
        assert reader.binary_store() is None
    finally:
        REAL_BINARY_STORE.cache_clear()


def test_requiring_a_store_doesnt_connect_so_redis_being_down_isnt_fatal(monkeypatch, real_store):
    """Nothing listens on 6399: building the clients still works, and only each read fails (keeping the last panel)."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    client = DeparturesClient(REGISTRATION, make_config(["490000123W"]))
    asyncio.run(client.initialise())

    with pytest.raises(RuntimeError, match="Could not fetch departures"):
        client._update()


def test_departures_come_from_the_store_with_one(store):
    client = DeparturesClient(REGISTRATION, make_config(["490000123W"]))
    assert isinstance(client.sources["tfl"], StoredTflSource)
    assert isinstance(client.sources["entur"], StoredEnturSource)


# --- TfL from the store -------------------------------------------------------------


def test_a_stored_bus_stop_draws_a_row_per_route_under_its_name_and_letter():
    arrivals = [bus("12", "Oxford Circus", 300), bus("12", "Oxford Circus", 60), bus("148", "Camberwell", 120)]
    panel = stored_panel(to_stop_departures(BUS_STOP, arrivals, NOW), "hero", NOW)
    # The soonest route first, each badged with its number, its later buses' minutes after its first's.
    assert panel.departures == [
        Departures(badge="12", line="12", destination="Oxford Circus", minutes=(1, 5)),
        Departures(badge="148", line="148", destination="Camberwell", minutes=(2,)),
    ]
    assert (panel.stop_name, panel.stop_letter, panel.is_tube, panel.icon) == ("Elephant & Castle", "W", False, "bus")


def test_a_stored_tube_station_draws_a_row_per_destination_under_the_roundel():
    arrivals = [
        train("Morden", 240),
        train("Edgware via Charing Cross", 90),
        train("Morden", 30),
        train("Terminates here", 10, destination_naptan_id=TUBE_STATION.naptan_id),
    ]
    panel = stored_panel(to_stop_departures(TUBE_STATION, arrivals, NOW), "hero", NOW)
    # Badged with the line's initial, a row per destination; the train terminating here isn't one.
    assert panel.departures == [
        Departures(badge="N", line="Northern", destination="Morden", minutes=(0, 4)),
        Departures(badge="N", line="Northern", destination="Edgware via Charing Cross", minutes=(1,)),
    ]
    assert (panel.stop_name, panel.stop_letter, panel.is_tube, panel.icon) == ("Kennington", None, True, "subway")


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


# --- Entur from the store -----------------------------------------------------------


def entur_settings(*stop_ids: str) -> DeparturesSettings:
    return DeparturesSettings(tuple(DepartureStop(source="entur", id=stop_id) for stop_id in stop_ids))


def drawn(panel: EnturArrivalPanel) -> tuple:
    """Everything an Entur panel draws from: its rows (with their icons), its header's icons, name and letter."""
    return (
        panel.departures,
        panel.icons,
        panel.icon,
        panel.mixed_modes,
        panel.stop_name,
        panel.stop_letter,
        panel.is_tube,
        panel.style,
    )


def stored_via_json(stored):
    """Return `stored` as a renderer reads it back from the store."""
    return type(stored).model_validate_json(stored.model_dump_json())


@responses.activate
@pytest.mark.parametrize("stop_id", [MAJORSTUEN_ID, QUAY_ID], ids=["stop place", "quay"])
@pytest.mark.parametrize("style", ["hero", "card"])
def test_a_stored_entur_stop_draws_as_enturs_own_would(stop_id, style):
    mock_entur()
    stop, departures = fetched(stop_id)
    stored = stored_via_json(entur_stop_departures(stop, departures, NOW))
    from_store = StoredEnturPanel(stored, style, NOW)
    from_entur = EnturArrivalPanel(stop, departures, style)
    assert drawn(from_store) == drawn(from_entur)


@responses.activate
def test_a_stored_stop_serving_several_modes_keeps_its_header_and_row_icons():
    mock_entur()
    stored = stored_via_json(entur_stop_departures(*fetched(MAJORSTUEN_ID), NOW))
    panel = StoredEnturPanel(stored, "hero", NOW)
    assert panel.icons == ["subway", "tram"]
    assert panel.mixed_modes
    assert [(d.line, d.icon) for d in panel.departures] == [("3", "subway"), ("19", "tram"), ("5", "subway")]


@responses.activate
def test_a_stored_entur_stop_counts_whole_seconds_as_enturs_client_does():
    # Asked 0.4 s after the minute: 1 min 59.6 s to go is "1 min" from Entur's client (it truncates), so from the
    # store too, rather than Departure.time_to_station's 120 s ("2 min").
    mock_entur()
    asked_at = NOW + timedelta(milliseconds=400)
    source = EnturSource(requests.Session())
    stop = source.resolve(QUAY_ID)
    departures = source.departures(stop, now=asked_at)
    stored = entur_stop_departures(stop, departures, asked_at)
    assert StoredEnturPanel(stored, "hero", asked_at).departures == EnturArrivalPanel(stop, departures).departures
    assert StoredEnturPanel(stored, "hero", asked_at).departures[0].minutes == (1,)


@responses.activate
def test_stored_entur_departures_count_down_and_drop_once_gone_as_enturs_client_would():
    mock_entur()
    stored = entur_stop_departures(*fetched(QUAY_ID), NOW)  # the 3 in 2 min, the 1 in 9 min
    # 2.5 min on, the 3 left 30 s ago: still shown, as due, as Entur's client would.
    assert [(d.line, d.minutes) for d in StoredEnturPanel(stored, "hero", NOW + timedelta(minutes=2.5)).departures] == [
        ("3", (0,)),
        ("1", (6,)),
    ]
    # 3.5 min on, it left 90 s ago: gone, as Entur's client leaves out anything over a minute gone.
    assert [(d.line, d.minutes) for d in StoredEnturPanel(stored, "hero", NOW + timedelta(minutes=3.5)).departures] == [
        ("1", (5,)),
    ]


@responses.activate
def test_the_stored_entur_source_reads_the_stops_key(store):
    mock_entur()
    store.set(keys.departures(QUAY_ID), entur_stop_departures(*fetched(QUAY_ID), NOW).model_dump_json())
    source = StoredEnturSource(store, now=lambda: NOW)
    panel = source.panel(source.resolve(QUAY_ID), "hero")
    assert (panel.stop_name, panel.stop_letter) == ("Majorstuen", "2")


def test_an_entur_stop_not_in_the_store_yet_still_resolves_but_raises_when_drawn(store):
    source = StoredEnturSource(store)
    stop = source.resolve("NSR:StopPlace:999")
    assert stop == "NSR:StopPlace:999"  # kept, so it's drawn once the provider has fetched it
    with pytest.raises(NoStoredDeparturesError):
        source.panel(stop, "hero")


@responses.activate
def test_departures_client_draws_stored_entur_stops_without_asking_entur(store):
    mock_entur()
    store.set(keys.departures(MAJORSTUEN_ID), entur_stop_departures(*fetched(MAJORSTUEN_ID), NOW).model_dump_json())
    asked_before = len(responses.calls)
    client = DeparturesClient(REGISTRATION, entur_settings(MAJORSTUEN_ID, "NSR:StopPlace:999"))
    asyncio.run(client.initialise())
    panel = client._update()
    assert [p.stop_name for p in panel.arrival_panels] == ["Majorstuen"]
    assert len(responses.calls) == asked_before


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
