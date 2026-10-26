"""The energy client's own workings; test_glow_store has it drawing the stored readings countdown-server writes."""

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from test_utils import TEST_DEVICE_ID

from countdown_api.models import Glowmarkt
from countdown_core.glow.glow_client import GlowClient, _bucket_starts
from countdown_core.store import keys, reader

CONFIG = Glowmarkt(enabled=True)


def make_client(device_id: str = TEST_DEVICE_ID) -> GlowClient:
    return GlowClient(device_id, CONFIG)


def test_bucket_starts_parse_epoch_and_iso_timestamps():
    readings = _bucket_starts([(1700000000, 1.5), ("2023-11-14T23:00:00", 2.25)], UTC)

    assert readings == [
        (datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC), 1.5),
        (datetime(2023, 11, 14, 23, 0, tzinfo=UTC), 2.25),
    ]


def test_bucket_starts_are_labelled_as_local_wall_clock():
    """Glow encodes each bucket's local wall-clock start as if it were UTC.

    With offset=-60, the 06:00 BST bucket comes back as the epoch for 06:00Z.
    """
    bst = timezone(timedelta(hours=1), "BST")
    data = [(int(datetime(2026, 9, 25, 6, tzinfo=UTC).timestamp()), 1.5), ("2026-09-25T07:00:00", 2.25)]

    assert _bucket_starts(data, bst) == [
        (datetime(2026, 9, 25, 6, tzinfo=bst), 1.5),
        (datetime(2026, 9, 25, 7, tzinfo=bst), 2.25),
    ]


def test_update_returns_a_panel_and_rotates_through_the_three_pages(store, rotation_clock):
    now = datetime.now(UTC)
    empty = {"data": []}
    stored = {
        "fetched_at": now.isoformat(),
        "device_id": TEST_DEVICE_ID,
        "offset": "0",
        "resource_id": "elec-id",
    }
    store.set(keys.energy(TEST_DEVICE_ID), json.dumps(stored | {"hourly": empty, "daily": empty, "monthly": empty}))
    client = make_client()

    pages = []
    for minute in range(4):
        rotation_clock.at_minute(minute)
        pages.append(asyncio.run(client.update()).page_index)

    assert pages == [0, 1, 2, 0]


def test_construction_reads_nothing(store, monkeypatch):
    monkeypatch.setattr(store, "get", lambda key: pytest.fail(f"read {key}"))

    assert make_client().device_id == TEST_DEVICE_ID


def test_without_a_store_it_cant_be_built_and_says_why(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)

    with pytest.raises(reader.StoreNotConfiguredError, match="REDIS_URL"):
        make_client()
