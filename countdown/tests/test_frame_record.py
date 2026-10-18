"""The screen's frame record in the store (countdown_core.store.frame_record), and the renderer writing it.

The record is written by the render (render.render), and the frame then shown through the real upload path:
DisplayController painting on ServerTarget, which PUTs it to the broker (mocked with `responses`), and DisplayLoop
driving them. The store is conftest's fake Redis, read back the way the broker reads it: as bytes.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest
import redis
import requests
import responses
from config_factory import make_config
from PIL import Image
from test_e2e_render_cycle import DEVICE_ID as E2E_DEVICE_ID
from test_e2e_render_cycle import _make_config, _put_stops_and_energy, _StopLoopError

from countdown_core import render as render_module
from countdown_core.config_server.broker_client import BrokerClient
from countdown_core.core.display import DisplayController
from countdown_core.core.display_loop import DisplayLoop
from countdown_core.render import Outcome, Result, render
from countdown_core.store import frame_record, keys, reader
from countdown_credentials.registration import RendererRegistration
from countdown_server.server_target import REFRESH_HINT_HEADER, ServerTarget

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "frame_record.json").read_text())
FIXTURE_FRAME = (FIXTURES / FIXTURE["fields"]["frame"]["file"]).read_bytes()

BROKER_URL = "https://broker.example.com"
FRAME_URL = f"{BROKER_URL}/api/frame"
DEVICE_ID = "device-123"
KEY = keys.frame(DEVICE_ID)
FRAME_BYTES = 800 * 480 // 8
AT = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class Clock:
    """When records are written (frame_record.now), stopped until a test moves it."""

    def __init__(self) -> None:
        self.at = AT

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float = 60) -> None:
        self.at += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(frame_record, "now", clock)
    return clock


@pytest.fixture
def stored(store):
    """Return the fake store as the broker reads the record: a client that doesn't decode values."""
    return reader.binary_store()


@pytest.fixture
def broker():
    """Mock the broker's PUT /api/frame, taking every frame."""
    with responses.RequestsMock() as rsps:
        rsps.put(FRAME_URL, status=204)
        yield rsps


def _target(device_id: str = DEVICE_ID) -> ServerTarget:
    return ServerTarget(RendererRegistration(BROKER_URL, "device-secret", device_id))


def _image(frame_bytes: bytes) -> Image.Image:
    """Return the picture whose panel bytes are `frame_bytes` (panel_bytes inverts PIL's 1-bit pixels)."""
    return Image.frombytes("1", (800, 480), bytes(b ^ 0xFF for b in frame_bytes))


def _puts(broker) -> list[bytes]:
    return [call.request.body for call in broker.calls if call.request.method == "PUT"]


# --- the frame's hash ---------------------------------------------------------------------------------------------


def test_the_hash_is_the_brokers_etag():
    # auth-broker's routes/frames.py: blake2b(frame, digest_size=8).hexdigest(), here for a blank and a black frame.
    assert frame_record.frame_etag(bytes(FRAME_BYTES)) == "cf4d49a0c56195f9"
    assert frame_record.frame_etag(b"\xff" * FRAME_BYTES) == "21404bb162874faf"


def test_different_frames_hash_differently():
    assert frame_record.frame_etag(bytes(FRAME_BYTES)) != frame_record.frame_etag(bytes(FRAME_BYTES - 1) + b"\x01")


# --- the shape, pinned by the fixture both repositories read ------------------------------------------------------


def test_the_key_is_the_fixtures():
    assert keys.frame(FIXTURE["device_id"]) == FIXTURE["key"]


def test_the_fixtures_frame_is_a_whole_frame_with_its_etag():
    assert len(FIXTURE_FRAME) == FIXTURE["fields"]["frame"]["length"] == FRAME_BYTES
    assert frame_record.frame_etag(FIXTURE_FRAME) == FIXTURE["fields"]["etag"]
    assert FIXTURE["served_etag"] == f'"{FIXTURE["fields"]["etag"]}"'
    with pytest.raises(UnicodeDecodeError):  # so only a client that doesn't decode can read it
        FIXTURE_FRAME.decode()


def test_the_fixtures_frame_is_written_as_the_fixtures_record(stored, clock):
    clock.at = datetime.fromisoformat(FIXTURE["fields"]["rendered_at"])

    assert frame_record.write_if_changed(stored, FIXTURE["device_id"], FIXTURE_FRAME) is True

    key = FIXTURE["key"]
    assert stored.type(key) == FIXTURE["type"].encode()
    assert stored.hgetall(key) == {
        b"frame": FIXTURE_FRAME,
        b"etag": FIXTURE["fields"]["etag"].encode(),
        b"rendered_at": FIXTURE["fields"]["rendered_at"].encode(),
    }
    assert stored.ttl(key) == FIXTURE["ttl_s"] == frame_record.TTL_S


def test_the_time_is_written_in_utc_whatever_zone_the_clock_gives(stored, clock):
    clock.at = datetime.fromisoformat("2026-10-18T13:00:00.000+01:00")

    frame_record.write_if_changed(stored, DEVICE_ID, FIXTURE_FRAME)

    assert stored.hget(KEY, "rendered_at") == FIXTURE["fields"]["rendered_at"].encode()


# --- writing only what changed --------------------------------------------------------------------------------------


def test_the_first_frame_is_written(stored, clock):
    assert frame_record.write_if_changed(stored, DEVICE_ID, bytes(FRAME_BYTES)) is True

    assert stored.hget(KEY, "frame") == bytes(FRAME_BYTES)


def test_an_unchanged_frame_is_not_written_again(stored, clock):
    frame_record.write_if_changed(stored, DEVICE_ID, bytes(FRAME_BYTES))
    clock.advance()
    stored.expire(KEY, 100)  # so a write would show, as it resets the TTL

    assert frame_record.write_if_changed(stored, DEVICE_ID, bytes(FRAME_BYTES)) is False

    assert stored.hget(KEY, "rendered_at") == AT.isoformat(timespec="milliseconds").encode()
    assert stored.ttl(KEY) == 100


def test_a_changed_frame_replaces_the_record(stored, clock):
    frame_record.write_if_changed(stored, DEVICE_ID, bytes(FRAME_BYTES))
    clock.advance()
    stored.expire(KEY, 100)

    assert frame_record.write_if_changed(stored, DEVICE_ID, b"\xff" * FRAME_BYTES) is True

    assert stored.hgetall(KEY) == {
        b"frame": b"\xff" * FRAME_BYTES,
        b"etag": b"21404bb162874faf",
        b"rendered_at": (AT + timedelta(seconds=60)).isoformat(timespec="milliseconds").encode(),
    }
    assert stored.ttl(KEY) == frame_record.TTL_S


def test_a_record_with_fields_it_doesnt_know_is_replaced_whole(stored, clock):
    stored.hset(KEY, mapping={"etag": "0000000000000000", "something": "else"})

    frame_record.write_if_changed(stored, DEVICE_ID, bytes(FRAME_BYTES))

    assert set(stored.hkeys(KEY)) == {b"frame", b"etag", b"rendered_at"}


def test_a_lapsed_record_is_written_again(stored, clock):
    frame_record.write_if_changed(stored, DEVICE_ID, bytes(FRAME_BYTES))
    stored.delete(KEY)  # expired, evicted, or Redis restarted

    assert frame_record.write_if_changed(stored, DEVICE_ID, bytes(FRAME_BYTES)) is True

    assert stored.hget(KEY, "etag") == b"cf4d49a0c56195f9"


def test_a_client_that_decodes_values_can_write_it_too(store, stored, clock):
    # The writer reads back only the etag, which is text either way.
    assert frame_record.write_if_changed(store, DEVICE_ID, FIXTURE_FRAME) is True
    assert frame_record.write_if_changed(store, DEVICE_ID, FIXTURE_FRAME) is False

    assert stored.hget(KEY, "frame") == FIXTURE_FRAME


def test_each_screen_has_its_own(stored, clock):
    frame_record.write_if_changed(stored, "device-1", bytes(FRAME_BYTES))
    frame_record.write_if_changed(stored, "device-2", b"\xff" * FRAME_BYTES)

    assert stored.hget(keys.frame("device-1"), "frame") == bytes(FRAME_BYTES)
    assert stored.hget(keys.frame("device-2"), "frame") == b"\xff" * FRAME_BYTES


# --- through the real upload path: the render, then DisplayController on ServerTarget -----------------------------


def _render(code: str = "ABC123", device_id: str = DEVICE_ID) -> Outcome:
    """Render a screen showing the pairing code `code`: a picture of its own for each code, read from nowhere."""
    return render(device_id, AT, config=make_config(pairing_code=code))


def _render_and_show(display: DisplayController, code: str = "ABC123") -> Outcome:
    outcome = _render(code)
    display.show(outcome.frame)
    return outcome


def test_each_new_picture_is_written_and_put(stored, clock, broker):
    display = DisplayController(_target())

    first = _render_and_show(display, "ABC123")
    clock.advance()
    second = _render_and_show(display, "XYZ789")

    assert (first.result, second.result) == (Result.WRITTEN, Result.WRITTEN)
    assert _puts(broker) == [first.data, second.data]
    assert len(second.data) == FRAME_BYTES
    assert stored.hget(KEY, "frame") == second.data
    assert stored.hget(KEY, "etag") == second.etag.encode() == frame_record.frame_etag(second.data).encode()
    assert stored.hget(KEY, "rendered_at") == (AT + timedelta(seconds=60)).isoformat(timespec="milliseconds").encode()


def test_an_unchanged_picture_writes_nothing(stored, clock, broker, monkeypatch):
    display = DisplayController(_target())
    _render_and_show(display)
    written_at = stored.hget(KEY, "rendered_at")
    writes = _count_writes(monkeypatch)

    clock.advance()
    outcomes = [_render_and_show(display), _render_and_show(display)]

    assert writes == [False, False]  # each checked, neither written
    assert [outcome.result for outcome in outcomes] == [Result.UNCHANGED, Result.UNCHANGED]
    assert stored.hget(KEY, "rendered_at") == written_at
    assert len(_puts(broker)) == 1


def test_an_unchanged_picture_puts_a_lost_record_back(stored, clock, broker):
    display = DisplayController(_target())
    first = _render_and_show(display)
    stored.delete(KEY)  # Redis restarted, or the record lapsed after a day of the same picture

    again = _render_and_show(display)

    assert again.result == Result.WRITTEN
    assert stored.hget(KEY, "frame") == first.data
    assert len(_puts(broker)) == 1  # the broker has it: no PUT


def test_renewing_a_pace_puts_again_but_writes_nothing(stored, clock, broker, monkeypatch):
    display = DisplayController(_target())
    display.set_pace(30)
    _render_and_show(display)
    writes = _count_writes(monkeypatch)

    _render_and_show(display)

    assert writes == [False]
    puts = [call for call in broker.calls if call.request.method == "PUT"]
    assert [call.request.headers[REFRESH_HINT_HEADER] for call in puts] == ["30", "30"]


def test_a_frame_the_broker_cant_take_still_reaches_the_record(stored, clock):
    with responses.RequestsMock() as rsps:
        rsps.put(FRAME_URL, status=503)
        display = DisplayController(_target())

        with pytest.raises(requests.HTTPError):  # the loop logs it and carries on, as before
            _render_and_show(display)

    assert stored.hget(KEY, "frame") == _render().data


def test_a_store_that_cant_be_written_still_lets_the_frame_be_put(clock, broker, monkeypatch):
    monkeypatch.setattr(reader, "binary_store", _unreachable)
    logged = _patch_logger(monkeypatch)

    outcome = _render_and_show(DisplayController(_target()))

    assert outcome.result == Result.UNRECORDED
    assert _puts(broker) == [outcome.data]
    assert logged.warning.call_args.args == ("Couldn't write the frame to the store (%s)", "ConnectionError")


def test_a_store_that_cant_be_written_is_tried_again_on_the_next_render(stored, clock, broker, monkeypatch):
    fail = SimpleNamespace(on=True)
    write = frame_record.write_if_changed

    def flaky(client, device_id, frame_bytes):
        if fail.on:
            raise redis.ConnectionError
        return write(client, device_id, frame_bytes)

    monkeypatch.setattr(frame_record, "write_if_changed", flaky)
    _patch_logger(monkeypatch)
    display = DisplayController(_target())
    first = _render_and_show(display)
    fail.on = False

    again = _render_and_show(display)  # the same picture: the record hasn't got it

    assert (first.result, again.result) == (Result.UNRECORDED, Result.WRITTEN)
    assert stored.hget(KEY, "frame") == first.data


def test_without_a_store_there_is_only_the_put(clock, broker, monkeypatch):
    monkeypatch.setattr(reader, "binary_store", lambda: None)

    outcome = _render_and_show(DisplayController(_target()))

    assert outcome.result == Result.UNRECORDED
    assert _puts(broker) == [outcome.data]


def test_painting_puts_the_frame_without_writing_a_record_itself(stored, clock, broker):
    """The record is the render's to write: the target only PUTs."""
    assert _target().paint(_image(FIXTURE_FRAME)) is True

    assert _puts(broker) == [FIXTURE_FRAME]
    assert not stored.exists(KEY)


def test_nothing_logged_says_what_the_frame_shows(clock, broker, monkeypatch):
    monkeypatch.setattr(reader, "binary_store", _unreachable)
    logged = _patch_logger(monkeypatch)

    _render_and_show(DisplayController(_target()))

    for call in logged.method_calls:
        assert all(not isinstance(arg, bytes | bytearray) for arg in call.args)
        assert DEVICE_ID not in " ".join(str(arg) for arg in call.args)
        assert "ABC123" not in " ".join(str(arg) for arg in call.args)


def _unreachable() -> fakeredis.FakeRedis:
    """Return a client of a Redis that's down: every command raises redis.ConnectionError."""
    server = fakeredis.FakeServer()
    server.connected = False
    return fakeredis.FakeRedis(server=server)


def _count_writes(monkeypatch) -> list[bool]:
    """Record what each write_if_changed returned: True for a write, False for one skipped."""
    seen: list[bool] = []
    write = frame_record.write_if_changed

    def counting(client, device_id, frame_bytes):
        seen.append(write(client, device_id, frame_bytes))
        return seen[-1]

    monkeypatch.setattr(frame_record, "write_if_changed", counting)
    return seen


def _patch_logger(monkeypatch) -> MagicMock:
    logged = MagicMock()
    monkeypatch.setattr(render_module, "logger", logged)
    return logged


# --- through the display loop -------------------------------------------------------------------------------------


def test_the_display_loop_keeps_the_record_holding_what_it_put(store, stored, symbols_font, monkeypatch):
    # The loop as the app runs it, on the server target: the real clients reading the store, drawing, painting.
    registration = RendererRegistration(BROKER_URL, "test-secret", E2E_DEVICE_ID)
    key = keys.frame(E2E_DEVICE_ID)
    config = _make_config()
    config.tfl.stop_ids = ["490000123W"]
    _put_stops_and_energy(store, "490000123W")
    broker_client = BrokerClient(registration)
    asyncio.run(broker_client.initialise())
    monkeypatch.setattr(broker_client, "get_config", lambda: config)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            stored.delete(key)  # lost between two renders: the second puts it back, changed picture or not
        else:
            raise _StopLoopError

    loop = DisplayLoop(broker_client, config, DisplayController(ServerTarget(registration)), sleep=fake_sleep)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.put(FRAME_URL, status=204)
        with pytest.raises(_StopLoopError):
            asyncio.run(loop.run())
        last_put = _puts(rsps)[-1]

    assert len(last_put) == FRAME_BYTES
    assert stored.hget(key, "frame") == last_put
    assert stored.hget(key, "etag") == frame_record.frame_etag(last_put).encode()
