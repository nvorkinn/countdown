"""A screen's live pairing code from the store (`v1:pairing:<device id>`, store.pairing), and the render drawing it.

The broker's copy of the config never carries the code, and there's none while a code is live, so a render that reads
the store (render(device_id, now)) draws the pairing screen from this key while it's there.
The store contract's pairing_key.json (countdown_api.fixtures) is the stored shape, which auth-broker's tests read too.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis
from config_factory import make_config

from countdown_api import fixtures
from countdown_api.models import ConfigResponse
from countdown_core import i18n
from countdown_core.render import Outcome, Result, Stage, render
from countdown_core.store import frame_record, keys, reader
from countdown_core.store import pairing as stored_pairing

FIXTURE = fixtures.load("pairing_key.json")
DEVICE_ID = FIXTURE["device_id"]
CODE = FIXTURE["value"]["pairing_code"]
PARITY = json.loads((Path(__file__).parent / "fixtures" / "render_parity.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)

pytestmark = pytest.mark.usefixtures("symbols_font")


@pytest.fixture(autouse=True)
def locale(monkeypatch):
    """Put the language and time zone back after each test: a render sets them, a module global."""
    monkeypatch.setattr(i18n, "_language", i18n.language())
    monkeypatch.setattr(i18n, "_timezone", i18n.local_zone())


def _put_code(store, value=None, device_id=DEVICE_ID) -> None:
    """Store a live code as the broker does: GET /api/config's answer with the code, expiring with it."""
    store.set(keys.pairing(device_id), json.dumps(value or FIXTURE["value"]), ex=FIXTURE["ttl_s"])


def _put_config(store, config: ConfigResponse) -> None:
    store.set(keys.config(config.device_id), config.model_dump_json())


# --- the key and its value ----------------------------------------------------------------------------------------


def test_the_key_is_the_one_the_broker_writes():
    assert keys.pairing(DEVICE_ID) == FIXTURE["key"]


def test_the_stored_code_reads_as_the_config_get_api_config_answers(store):
    _put_code(store)

    stored = stored_pairing.read(store, DEVICE_ID)

    assert stored == ConfigResponse.model_validate(FIXTURE["value"])
    assert stored.pairing_code == CODE


def test_no_key_is_no_code(store):
    assert stored_pairing.read(store, DEVICE_ID) is None


def test_a_stored_config_without_a_code_is_no_code(store):
    _put_code(store, {**FIXTURE["value"], "pairing_code": None})

    assert stored_pairing.read(store, DEVICE_ID) is None


# --- the render, reading the store (the pool's) --------------------------------------------------------------------


def test_a_live_code_draws_the_pairing_screen_with_no_config_in_the_store(store):
    _put_code(store)

    outcome = render(DEVICE_ID, NOW)

    assert outcome.stage is Stage.PAIRING
    assert outcome.result is Result.WRITTEN


def test_it_draws_the_frame_the_old_loop_drew_for_its_code(store):
    """The parity fixture's pairing screen (render_parity.json), drawn from the store rather than handed in."""
    [case] = [case for case in PARITY["cases"] if case["config"]["pairing_code"]]
    _put_code(store, case["config"], PARITY["device_id"])

    outcome = render(PARITY["device_id"], datetime.fromisoformat(case["now"]))

    assert outcome.etag == case["etag"]
    assert reader.binary_store().hget(keys.frame(PARITY["device_id"]), frame_record.ETAG) == case["etag"].encode()


def test_a_live_code_wins_over_the_config_in_the_store(store):
    _put_config(store, make_config(device_id=DEVICE_ID))
    _put_code(store)

    assert render(DEVICE_ID, NOW).stage is Stage.PAIRING


def test_with_the_code_gone_the_screen_is_its_dashboard_again(store):
    _put_config(store, make_config(device_id=DEVICE_ID))
    _put_code(store)
    render(DEVICE_ID, NOW)

    store.delete(keys.pairing(DEVICE_ID))  # used, or lapsed

    assert render(DEVICE_ID, NOW).stage is Stage.RUNNING


def test_with_the_code_gone_and_no_config_there_is_nothing_to_draw(store):
    assert render(DEVICE_ID, NOW) == Outcome(Result.NO_CONFIG)


def test_redis_not_answering_raises_as_for_the_config(store, monkeypatch):
    def down(*_args):
        raise redis.ConnectionError

    monkeypatch.setattr(store, "get", down)

    with pytest.raises(redis.RedisError):
        render(DEVICE_ID, NOW)


def test_no_code_is_logged(store, caplog):
    _put_code(store)

    with caplog.at_level(logging.DEBUG):
        render(DEVICE_ID, NOW)

    assert CODE not in caplog.text


def test_the_code_never_goes_in_the_config_key(store):
    _put_code(store)

    render(DEVICE_ID, NOW)

    assert store.get(keys.config(DEVICE_ID)) is None
