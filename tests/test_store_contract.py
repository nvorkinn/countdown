"""Our side of the store contract, through its canonical examples (countdown-api's countdown_api.fixtures).

countdown-server writes most of what crosses between the two repositories, and reads the rest; its tests check its side
against the same files. Here each of our readers reads the example the broker would have written, under the
contract's key, and each of its writers writes exactly the example the broker reads, with the contract's TTL. So
neither side can move alone: a change to the stored shape is a change to the contract first.
"""

import asyncio
import json
import re
from datetime import datetime

import fakeredis
import pytest
import requests
from config_factory import CONFIGS
from pydantic import BaseModel, TypeAdapter

from countdown_api import fixtures, models, scripts
from countdown_api import keys as contract_keys
from countdown_providers.bank_holidays.main import BankHolidaysProvider
from countdown_providers.entur.main import EnturProvider
from countdown_providers.locations.reader import LocationReader
from countdown_providers.met_office.main import MetOfficeProvider
from countdown_providers.runner import PACE_TTL_S, ProviderRunner
from countdown_providers.tfl.main import TflProvider
from countdown_providers.tfl_notices.main import TflLineStatusProvider, TflRoadProvider, TflStationProvider
from countdown_providers.weather.main import WeatherProvider
from countdown_shared.entur.entur_source import EnturSource
from countdown_shared.store import config, developer, frame_record, keys, pairing, parcels, reader, render_queue
from countdown_shared.store.bank_holidays import StoredBankHolidays
from countdown_shared.store.energy import StoredEnergy
from countdown_shared.store.met_office import StoredMetOfficeWarnings
from countdown_shared.store.models import StopDepartures, StoredForecast
from countdown_shared.store.music import StoredPlayer, StoredTop
from countdown_shared.store.tfl_notices import StoredLineStatus, StoredRoadDisruptions, StoredStationDisruptions
from countdown_shared.tfl.tfl_client import TflSource

CONFIG = fixtures.load("config_store.json")
PAIRING = fixtures.load("pairing_key.json")


@pytest.fixture
def client():
    return fakeredis.FakeRedis(decode_responses=True)


def test_the_keys_that_cross_are_the_contracts():
    for name in (
        "subscription",
        "config",
        "pairing",
        "render_queued",
        "frame",
        "energy",
        "spotify",
        "spotify_top",
        "album_art",
        "developer",
        "parcels",
        "pace",
        "departures",
        "weather",
        "tfl_line_status",
        "tfl_stations",
        "tfl_roads",
        "met_office",
    ):
        assert getattr(keys, name) is getattr(contract_keys, name)
    assert keys.BANK_HOLIDAYS == contract_keys.BANK_HOLIDAYS
    assert (keys.SUB_CHANGED, keys.RENDER_JOBS, keys.RENDER_SCREENS) == (
        contract_keys.SUB_CHANGED,
        contract_keys.RENDER_JOBS,
        contract_keys.RENDER_SCREENS,
    )
    assert keys.SOURCES == contract_keys.SOURCES
    assert render_queue.QUEUED_TTL_MS == contract_keys.RENDER_QUEUED_TTL_MS
    assert frame_record.TTL_S == contract_keys.FRAME_TTL_S
    assert PACE_TTL_S == contract_keys.PACE_TTL_S


# --- what the broker writes, read here ---------------------------------------------------------------------------


def test_the_render_reads_the_config_the_broker_stores(client):
    client.set(CONFIG["key"], json.dumps(CONFIG["value"]))

    stored = config.read(client, CONFIG["device_id"])

    assert stored == CONFIGS.validate_python(CONFIG["value"])
    assert json.loads(stored.model_dump_json()) == CONFIG["value"]


def test_the_render_reads_the_pairing_code_the_broker_stores(client):
    client.set(PAIRING["key"], json.dumps(PAIRING["value"]), ex=PAIRING["ttl_s"])

    stored = pairing.read(client, PAIRING["device_id"])

    assert stored is not None
    assert stored.pairing_code == PAIRING["value"]["pairing_code"]
    assert json.loads(stored.model_dump_json()) == PAIRING["value"]
    # The stored config has no code, so it's no pairing value.
    client.set(keys.pairing(CONFIG["device_id"]), json.dumps(CONFIG["value"]))
    assert pairing.read(client, CONFIG["device_id"]) is None


def test_the_pool_reads_the_screens_the_broker_lists(client):
    example = fixtures.load("render_screens.json")
    client.hset(example["key"], mapping=example["screens"])

    assert render_queue.screens(client) == example["screens"]


def test_the_renderers_read_the_broker_providers_data(client):
    energy = fixtures.load("glow_energy.json")
    client.set(keys.energy(energy["device_id"]), json.dumps(energy["stored"]))
    assert reader.read(client, keys.energy(energy["device_id"]), StoredEnergy).device_id == energy["device_id"]

    device_id = CONFIG["device_id"]
    client.set(keys.spotify(device_id), json.dumps(fixtures.load("music_player.json")))
    client.set(keys.spotify_top(device_id), json.dumps(fixtures.load("music_top.json")))
    client.set(keys.developer(device_id), json.dumps(fixtures.load("developer.json")))
    player = reader.read(client, keys.spotify(device_id), StoredPlayer)
    assert player.spotify.currently_playing is not None
    assert player.apple_music.currently_playing is not None
    top = reader.read(client, keys.spotify_top(device_id), StoredTop)
    assert set(top.spotify) == {"long_term", "short_term"}
    assert developer.read(client, device_id).github is not None

    parcel_list = fixtures.load("parcels.json")
    client.set(keys.parcels(device_id), json.dumps(parcel_list["value"]))
    assert [p.status for p in parcels.read(client, device_id).parcels] == [
        p["status"] for p in parcel_list["value"]["parcels"]
    ]


def test_the_spotify_area_reads_the_covers_by_their_urls_key():
    binary = fakeredis.FakeRedis()
    art = fixtures.read_bytes("music_art.jpg")
    for cover in fixtures.load("music_art.json").values():
        binary.set(cover["key"], art, ex=contract_keys.ALBUM_ART_TTL_S)
        assert binary.get(keys.album_art(cover["url"])) == art


def test_each_provider_reads_its_sources_subscription_set():
    example = fixtures.load("subscriptions.json")
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    for key, members in example["sets"].items():
        asyncio.run(redis.sadd(key, *members))

    class Reader:
        interval_s = 60

        def __init__(self, name):
            self.name = name

    for source in keys.SOURCES:
        wanted = asyncio.run(ProviderRunner(Reader(source), redis)._wanted())
        assert wanted == set(example["sets"][keys.subscription(source)])
    assert ProviderRunner(Reader(keys.TFL), redis).wakes_on == (example["channel"],)


# --- what this side writes, read by the broker -------------------------------------------------------------------


def test_the_render_writes_the_frame_record_the_broker_serves(monkeypatch):
    example = fixtures.load("frame_record.json")
    frame = fixtures.read_bytes(example["fields"]["frame"]["file"])
    monkeypatch.setattr(frame_record, "now", lambda: datetime.fromisoformat(example["fields"]["rendered_at"]))
    binary = fakeredis.FakeRedis()

    assert frame_record.write_if_changed(binary, example["device_id"], frame) is True

    assert binary.type(example["key"]) == example["type"].encode()
    stored = {field.decode(): value for field, value in binary.hgetall(example["key"]).items()}
    assert stored == {"frame": frame, **{name: example["fields"][name].encode() for name in ("etag", "rendered_at")}}
    assert binary.ttl(example["key"]) == example["ttl_s"]
    record = models.FrameRecord.model_validate(
        {**stored, "etag": stored["etag"].decode(), "rendered_at": stored["rendered_at"].decode()}
    )
    assert f'"{record.etag}"' == example["served_etag"]


def test_enqueue_writes_the_job_and_flag_the_contract_says(client):
    example = fixtures.load("render_queue.json")
    device_id = example["device_id"]

    assert render_queue.enqueue(client, device_id) is True

    assert client.script_exists(example["enqueue_sha1"]) == [True]
    assert example["enqueue_sha1"] == scripts.ENQUEUE.sha1
    assert client.lrange(example["jobs_key"], 0, -1) == [device_id]
    assert re.fullmatch(r"[0-9]+", client.get(example["queued_key"]))
    assert 0 < client.pttl(example["queued_key"]) <= example["queued_ttl_ms"]
    assert TypeAdapter(models.RenderJobs).validate_python(client.lrange(example["jobs_key"], 0, -1)) == [device_id]
    assert render_queue.next_job(client, 1) == device_id


def test_a_provider_writes_its_pace_as_the_broker_reads_it():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)

    class Bursty:
        name = keys.DEVELOPER
        interval_s = 60
        ttl_s = 120

        def output_key(self, item_id):
            return keys.developer(item_id)

        async def fetch(self, ids):
            return {item_id: models.StoredDeveloper.model_validate(fixtures.load("developer.json")) for item_id in ids}

        def pace(self, item_id, fetched):
            return 20

    device_id = CONFIG["device_id"]
    asyncio.run(redis.sadd(keys.subscription(keys.DEVELOPER), device_id))
    asyncio.run(ProviderRunner(Bursty(), redis).run_cycle())

    key = contract_keys.pace(keys.DEVELOPER, device_id)
    assert asyncio.run(redis.get(key)) == "20"
    assert 0 < asyncio.run(redis.ttl(key)) <= contract_keys.PACE_TTL_S


# --- what this side's providers write, read by the broker --------------------------------------------------------
#
# countdown's own models are richer than the contract's (methods, TfL's and Open-Meteo's typed models), so they aren't
# the contract's; but each reads the contract's example and dumps it back unchanged, so what a provider writes is
# exactly what the example shows. And each provider writes it under the example's key, with its TTL.

SESSION = requests.Session()
LOCATIONS = LocationReader(fakeredis.FakeAsyncRedis())
TFL = TflProvider(TflSource(SESSION))
ENTUR = EnturProvider(EnturSource(SESSION))
DEPARTURES = fixtures.load("departures.json")
WEATHER = fixtures.load("weather.json")
TFL_NOTICES = fixtures.load("tfl_notices.json")
MET_OFFICE = fixtures.load("met_office.json")
BANK_HOLIDAYS = fixtures.load("bank_holidays.json")


def assert_round_trips[M: BaseModel](model: type[M], value: dict) -> M:
    """Read the example's `value` as `model`, and check it dumps back to the same JSON."""
    stored = model.model_validate_json(json.dumps(value))
    assert json.loads(stored.model_dump_json()) == value
    return stored


@pytest.mark.parametrize("stop", DEPARTURES["stops"], ids=[stop["key"] for stop in DEPARTURES["stops"]])
def test_the_departures_providers_write_the_contracts_departures(stop):
    stored = assert_round_trips(StopDepartures, stop["value"])

    provider = ENTUR if stored.stop_id.startswith("NSR:") else TFL
    assert provider.output_key(stored.stop_id) == stop["key"]
    assert provider.ttl_s == DEPARTURES["ttl_s"]


def test_the_weather_provider_writes_the_contracts_forecast():
    assert_round_trips(StoredForecast, WEATHER["value"])

    provider = WeatherProvider(SESSION, LOCATIONS)
    assert provider.output_key(WEATHER["weather_key"]) == WEATHER["key"]
    assert provider.ttl_s == WEATHER["ttl_s"]


@pytest.mark.parametrize(
    ("name", "model", "provider", "item"),
    [
        ("lines", StoredLineStatus, TflLineStatusProvider(TflSource(SESSION)), "stop_id"),
        ("stations", StoredStationDisruptions, TflStationProvider(TflSource(SESSION)), "stop_id"),
        ("roads", StoredRoadDisruptions, TflRoadProvider(TflSource(SESSION), LOCATIONS), "postcode"),
    ],
)
def test_the_tfl_notices_providers_write_the_contracts_notices(name, model, provider, item):
    example = TFL_NOTICES[name]
    assert_round_trips(model, example["value"])

    assert provider.output_key(TFL_NOTICES[item]) == example["key"]
    assert provider.ttl_s == example["ttl_s"]


def test_the_met_office_provider_writes_the_contracts_warnings():
    assert_round_trips(StoredMetOfficeWarnings, MET_OFFICE["value"])

    provider = MetOfficeProvider(SESSION, LOCATIONS)
    assert provider.output_key(MET_OFFICE["postcode"]) == MET_OFFICE["key"]
    assert provider.ttl_s == MET_OFFICE["ttl_s"]


def test_the_bank_holidays_provider_writes_the_contracts_bank_holidays():
    assert_round_trips(StoredBankHolidays, BANK_HOLIDAYS["value"])

    provider = BankHolidaysProvider(SESSION)
    assert provider.output_key(provider.fixed_ids[0]) == BANK_HOLIDAYS["key"]
    assert provider.ttl_s == BANK_HOLIDAYS["ttl_s"]
