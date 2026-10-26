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
from pydantic import TypeAdapter

from countdown_api import fixtures, models, scripts
from countdown_api import keys as contract_keys
from countdown_core.store import config, developer, frame_record, keys, pairing, reader, render_queue
from countdown_core.store.energy import StoredEnergy
from countdown_core.store.music import StoredPlayer, StoredTop
from countdown_providers.runner import PACE_TTL_S, ProviderRunner

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
        "pace",
    ):
        assert getattr(keys, name) is getattr(contract_keys, name)
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

    assert stored == models.ConfigResponse.model_validate(CONFIG["value"])
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
