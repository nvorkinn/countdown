"""The bins provider against mocked Binable (responses) and fakeredis: what it asks, and what it stores.

fixtures/binable_oslo.json is Binable's answer for an Oslo address, each kind of waste at the top level.
"""

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import fakeredis
import pytest
import requests
import responses

from countdown_providers.bins.main import BINABLE_URL, BinsProvider, collections, parse_member
from countdown_providers.runner import ProviderRunner
from countdown_shared.store import keys
from countdown_shared.store.bins import StoredBinCollections

ANSWER = json.loads((Path(__file__).parent / "fixtures" / "binable_oslo.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
OSLO = "Thorvald Meyers gate|10|0555|Oslo"
OTHER = "Sognsveien|80|0855|Oslo"


def provider(api_key: str | None = "test-key") -> BinsProvider:
    return BinsProvider(requests.Session(), api_key, now=lambda: NOW)


def fetch(bins: BinsProvider, ids: list[str]) -> dict[str, StoredBinCollections]:
    return asyncio.run(bins.fetch(ids))


@pytest.mark.parametrize(
    ("member", "fields"),
    [
        (OSLO, {"street": "Thorvald Meyers gate", "houseNumber": "10", "zip": "0555", "city": "Oslo", "country": "NO"}),
        ("Storgata|12B|0184|Oslo", {"street": "Storgata", "houseNumber": "12B", "zip": "0184", "city": "Oslo"}),
        ("Storgata|12|0184", None),
        ("Storgata||0184|Oslo", None),
    ],
)
def test_a_member_is_binables_address_fields(member, fields):
    parsed = parse_member(member)
    if fields is None:
        assert parsed is None
    else:
        assert fields.items() <= parsed.items()


@responses.activate
def test_an_address_is_asked_for_with_the_key_and_its_days_ahead_are_stored():
    responses.post(BINABLE_URL, json=ANSWER)
    [stored] = fetch(provider(), [OSLO]).values()
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "ApiKey test-key"
    assert json.loads(request.body)["street"] == "Thorvald Meyers gate"
    assert stored.found
    assert stored.provider == ANSWER["provider"]["name"]
    assert [(c.fraction, c.dates[0]) for c in stored.collections] == [
        ("paper", date(2026, 11, 26)),
        ("residualWaste", date(2026, 11, 23)),
    ]


@responses.activate
def test_without_a_key_binable_is_asked_anonymously():
    responses.post(BINABLE_URL, json=ANSWER)
    fetch(provider(None), [OSLO])
    assert "Authorization" not in responses.calls[0].request.headers


def test_the_nested_shape_is_read_too_and_past_days_are_dropped():
    answer = {
        "typeMap": {"paper": "Papiermüll"},
        "collections": {
            "paper": {"dates": ["2026-11-20", "2026-11-27"]},
            "bio": {"dates": ["2026-11-01"]},
        },
    }
    assert [(c.fraction, c.dates) for c in collections(answer, date(2026, 11, 22))] == [("paper", [date(2026, 11, 27)])]


@pytest.mark.parametrize("status", [400, 404])
@responses.activate
def test_an_address_binable_has_no_calendar_for_is_stored_as_not_found(status):
    responses.post(BINABLE_URL, status=status, json={"error": f"Provider found, but no result for: {OTHER}"})
    stored = fetch(provider(), [OTHER])[OTHER]
    assert (stored.found, stored.collections) == (False, [])


@responses.activate
def test_after_a_429_the_rest_wait_for_the_next_cycle(caplog):
    responses.post(BINABLE_URL, status=429, json={"error": "To many requests / Rate Limiter"})
    with caplog.at_level(logging.WARNING):
        assert fetch(provider(), [OSLO, OTHER]) == {}
    assert len(responses.calls) == 1
    assert "2 addresses wait" in caplog.text


@responses.activate
def test_a_failed_address_is_left_out_and_no_address_is_logged(caplog):
    responses.post(BINABLE_URL, status=500, json={"error": f"Something about {OSLO}"})
    responses.post(BINABLE_URL, json=ANSWER)
    with caplog.at_level(logging.DEBUG):
        results = fetch(provider(), [OSLO, OTHER])
    assert list(results) == [OTHER]
    for text in ("Thorvald", "Sognsveien", "0555", "0855"):
        assert text not in caplog.text


@responses.activate
def test_the_runner_writes_each_address_under_its_hash():
    responses.post(BINABLE_URL, json=ANSWER)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    asyncio.run(redis.sadd(keys.subscription(keys.ADDRESS), OSLO))
    runner = ProviderRunner(provider(), redis)
    asyncio.run(runner.run_cycle())
    value = asyncio.run(redis.get(keys.bins(OSLO)))
    assert StoredBinCollections.model_validate_json(value).found
    assert 0 < asyncio.run(redis.ttl(keys.bins(OSLO))) <= keys.BINS_TTL_S
