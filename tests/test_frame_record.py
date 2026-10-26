"""The screen's frame record in the store (countdown_core.store.frame_record), and the render writing it.

The record is written by the render (render.render), the only way a frame reaches the broker. The store is conftest's
fake Redis, read back the way the broker reads it: as bytes.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest
import redis
from config_factory import make_config
from test_e2e_render_cycle import DEVICE_ID as E2E_DEVICE_ID
from test_e2e_render_cycle import _make_config, _put_stops_and_energy

from countdown_api import fixtures
from countdown_core import render as render_module
from countdown_core.render import Outcome, Result, render
from countdown_core.store import frame_record, keys, reader

FIXTURE = fixtures.load("frame_record.json")
FIXTURE_FRAME = fixtures.read_bytes(FIXTURE["fields"]["frame"]["file"])

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


# --- the frame's hash ---------------------------------------------------------------------------------------------


def test_the_hash_is_the_brokers_etag():
    # The broker's routes/frames.py: blake2b(frame, digest_size=8).hexdigest(), here for a blank and a black frame.
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


# --- through the render ------------------------------------------------------------------------------------------


def _render(code: str = "ABC123", device_id: str = DEVICE_ID) -> Outcome:
    """Render a screen showing the pairing code `code`: a picture of its own for each code, read from nowhere else."""
    reader.store().set(keys.pairing(device_id), make_config(pairing_code=code).model_dump_json())
    return render(device_id, AT)


def test_each_new_picture_is_written(stored, clock):
    first = _render("ABC123")
    clock.advance()
    second = _render("XYZ789")

    assert (first.result, second.result) == (Result.WRITTEN, Result.WRITTEN)
    assert len(second.data) == FRAME_BYTES
    assert stored.hget(KEY, "frame") == second.data
    assert stored.hget(KEY, "etag") == second.etag.encode() == frame_record.frame_etag(second.data).encode()
    assert stored.hget(KEY, "rendered_at") == (AT + timedelta(seconds=60)).isoformat(timespec="milliseconds").encode()


def test_an_unchanged_picture_writes_nothing(stored, clock, monkeypatch):
    _render()
    written_at = stored.hget(KEY, "rendered_at")
    writes = _count_writes(monkeypatch)

    clock.advance()
    outcomes = [_render(), _render()]

    assert writes == [False, False]  # each checked, neither written
    assert [outcome.result for outcome in outcomes] == [Result.UNCHANGED, Result.UNCHANGED]
    assert stored.hget(KEY, "rendered_at") == written_at


def test_an_unchanged_picture_puts_a_lost_record_back(stored, clock):
    first = _render()
    stored.delete(KEY)  # Redis restarted, or the record lapsed after a day of the same picture

    again = _render()

    assert again.result == Result.WRITTEN
    assert stored.hget(KEY, "frame") == first.data


def test_a_store_that_cant_be_written_is_logged_and_the_frame_returned(store, clock, monkeypatch):
    monkeypatch.setattr(reader, "binary_store", _unreachable)
    logged = _patch_logger(monkeypatch)

    outcome = _render()

    assert outcome.result == Result.UNRECORDED
    assert len(outcome.data) == FRAME_BYTES
    assert logged.warning.call_args.args == ("Couldn't write the frame to the store (%s)", "ConnectionError")


def test_a_store_that_cant_be_written_is_tried_again_on_the_next_render(stored, clock, monkeypatch):
    fail = SimpleNamespace(on=True)
    write = frame_record.write_if_changed

    def flaky(client, device_id, frame_bytes):
        if fail.on:
            raise redis.ConnectionError
        return write(client, device_id, frame_bytes)

    monkeypatch.setattr(frame_record, "write_if_changed", flaky)
    _patch_logger(monkeypatch)
    first = _render()
    fail.on = False

    again = _render()  # the same picture: the record hasn't got it

    assert (first.result, again.result) == (Result.UNRECORDED, Result.WRITTEN)
    assert stored.hget(KEY, "frame") == first.data


def test_nothing_logged_says_what_the_frame_shows(store, clock, monkeypatch):
    monkeypatch.setattr(reader, "binary_store", _unreachable)
    logged = _patch_logger(monkeypatch)

    _render()

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


# --- a dashboard, through the real clients -------------------------------------------------------------------------


def test_the_render_keeps_the_record_holding_what_it_drew(store, stored, symbols_font):
    """The real clients reading the store and drawing a dashboard; the record lost between two renders comes back."""
    key = keys.frame(E2E_DEVICE_ID)
    config = _make_config()
    config.tfl.stop_ids = ["490000123W"]
    _put_stops_and_energy(store, "490000123W")
    store.set(keys.config(E2E_DEVICE_ID), config.model_dump_json())

    render(E2E_DEVICE_ID, datetime.now(UTC))
    stored.delete(key)  # lost between two renders: the second puts it back, changed picture or not
    last = render(E2E_DEVICE_ID, datetime.now(UTC))

    assert last.result is Result.WRITTEN
    assert len(last.data) == FRAME_BYTES
    assert stored.hget(key, "frame") == last.data
    assert stored.hget(key, "etag") == frame_record.frame_etag(last.data).encode()
