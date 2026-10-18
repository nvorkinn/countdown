"""The MetAlerts provider against mocked MET (responses) and fakeredis: what it asks, when, and what it stores.

fixtures/metalerts_current.json is what it stores: MET's answers for all of Norway, as they came. The renderer's tests
(test_metalerts_store.py) read the same file.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
import requests
import responses

from countdown_core.notices.norway import MET_HEADERS, METALERTS_URL
from countdown_core.store import keys
from countdown_core.store.metalerts import StoredMetAlerts
from countdown_providers.metalerts.main import WITHOUT_EXPIRES, MetAlertsProvider, is_norwegian
from countdown_providers.runner import ProviderRunner

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "metalerts_current.json").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(FIXTURE["fetched_at"])
LAST_MODIFIED = "Fri, 16 Oct 2026 07:00:00 GMT"


class Clock:
    """A clock the tests move on by hand."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def http_date(when: datetime) -> str:
    return format_datetime(when.astimezone(UTC), usegmt=True)


def answer_met(lang: str, *, expires: datetime | None = NOW + timedelta(minutes=2), status: int = 200) -> None:
    """Have MET answer current.json in `lang` with the fixture's answer (or just `status`), current until `expires`."""
    headers = {"Last-Modified": LAST_MODIFIED}
    if expires is not None:
        headers["Expires"] = http_date(expires)
    if status == 200:
        responses.get(METALERTS_URL, json=FIXTURE[lang], headers=headers, match=[lang_is(lang)])
    else:
        responses.get(METALERTS_URL, status=status, headers=headers, match=[lang_is(lang)])


def lang_is(lang: str):
    def match(request):
        query = parse_qs(urlparse(request.url).query)
        return (query == {"lang": [lang]}, f"not lang={lang} alone: {request.url}")

    return match


def answer_both(**kwargs) -> None:
    for lang in ("no", "en"):
        answer_met(lang, **kwargs)


def fetch(provider: MetAlertsProvider, ids: list[str]) -> dict[str, StoredMetAlerts]:
    return asyncio.run(provider.fetch(ids))


@pytest.mark.parametrize(
    ("postcode", "norwegian"),
    [("0150", True), ("9008", True), ("SW1A 1AA", False), ("N1 9GU", False), ("015", False), ("01500", False)],
)
def test_a_norwegian_postcode_is_four_digits(postcode, norwegian):
    assert is_norwegian(postcode) is norwegian


@responses.activate
def test_met_is_asked_for_all_of_norway_in_both_languages_and_its_answers_stored_as_they_came():
    answer_both()
    results = fetch(MetAlertsProvider(requests.Session(), now=Clock()), ["0150", "9008", "SW1A 1AA"])
    # Under one postcode: the runner writes a result per id, and they'd all be the same key.
    assert list(results) == ["0150"]
    # MET's answers as they came, every field kept: the fixture, which the renderer's tests read.
    stored = json.loads(results["0150"].model_dump_json())
    assert datetime.fromisoformat(stored.pop("fetched_at")) == NOW
    assert stored == {key: value for key, value in FIXTURE.items() if key != "fetched_at"}
    # No place: one answer for the whole country, whichever postcodes the boards have.
    assert [parse_qs(urlparse(call.request.url).query) for call in responses.calls] == [
        {"lang": ["no"]},
        {"lang": ["en"]},
    ]
    for call in responses.calls:
        assert call.request.headers["User-Agent"] == MET_HEADERS["User-Agent"]
        assert "If-Modified-Since" not in call.request.headers


@responses.activate
def test_without_a_norwegian_postcode_met_is_asked_nothing():
    assert fetch(MetAlertsProvider(requests.Session(), now=Clock()), ["SW1A 1AA", "N1 9GU"]) == {}
    assert fetch(MetAlertsProvider(requests.Session(), now=Clock()), []) == {}
    assert len(responses.calls) == 0


def test_every_postcode_writes_the_one_key():
    provider = MetAlertsProvider(requests.Session())
    assert provider.output_key("0150") == provider.output_key("9008") == keys.metalerts() == "v1:notices:metalerts"
    assert provider.name == keys.POSTCODE
    assert provider.ttl_s > 3 * provider.interval_s


@responses.activate
def test_an_answer_is_kept_until_its_expires_then_asked_for_again_if_modified_since():
    clock = Clock()
    provider = MetAlertsProvider(requests.Session(), now=clock)
    answer_both(expires=NOW + timedelta(minutes=2))
    first = fetch(provider, ["0150"])["0150"]

    clock.now = NOW + timedelta(minutes=1)  # another batch, or a nudge: still current, so MET isn't asked
    assert fetch(provider, ["9008"])["9008"] == first
    assert len(responses.calls) == 2

    clock.now = NOW + timedelta(minutes=5)
    responses.reset()
    answer_both(expires=clock.now + timedelta(minutes=2), status=304)
    again = fetch(provider, ["0150"])["0150"]
    assert [call.request.headers["If-Modified-Since"] for call in responses.calls] == [LAST_MODIFIED] * 2
    # MET's answer is unchanged, and as of now.
    assert (again.no, again.en) == (first.no, first.en)
    assert again.fetched_at == clock.now


@responses.activate
def test_a_changed_answer_replaces_the_last():
    clock = Clock()
    provider = MetAlertsProvider(requests.Session(), now=clock)
    answer_both()
    fetch(provider, ["0150"])

    clock.now = NOW + timedelta(minutes=5)
    responses.reset()
    calm = {**FIXTURE["en"], "features": []}
    answer_met("no")
    responses.get(
        METALERTS_URL, json=calm, headers={"Last-Modified": "Fri, 16 Oct 2026 12:04:00 GMT"}, match=[lang_is("en")]
    )
    stored = fetch(provider, ["0150"])["0150"]
    assert stored.en == calm
    assert stored.no == FIXTURE["no"]


@responses.activate
def test_an_answer_without_an_expires_is_kept_only_briefly():
    clock = Clock()
    provider = MetAlertsProvider(requests.Session(), now=clock)
    answer_both(expires=None)
    fetch(provider, ["0150"])
    clock.now = NOW + WITHOUT_EXPIRES / 2
    fetch(provider, ["0150"])
    assert len(responses.calls) == 2
    clock.now = NOW + WITHOUT_EXPIRES
    fetch(provider, ["0150"])
    assert len(responses.calls) == 4


@responses.activate
def test_fetched_at_is_when_met_last_confirmed_the_older_answer():
    clock = Clock()
    provider = MetAlertsProvider(requests.Session(), now=clock)
    answer_met("no", expires=NOW + timedelta(hours=1))
    answer_met("en", expires=NOW + timedelta(minutes=2))
    fetch(provider, ["0150"])

    clock.now = NOW + timedelta(minutes=5)
    responses.reset()
    answer_met("en", status=304)
    stored = fetch(provider, ["0150"])["0150"]
    assert stored.fetched_at == NOW  # the Norwegian one, still current by its Expires, came then


@responses.activate
@pytest.mark.parametrize("status", [403, 429, 500])
def test_met_failing_fails_the_batch_so_the_key_keeps_its_last_answer(status):
    responses.get(METALERTS_URL, status=status)
    with pytest.raises(requests.HTTPError):
        fetch(MetAlertsProvider(requests.Session(), now=Clock()), ["0150"])


@responses.activate
def test_a_cycle_writes_the_one_key_with_its_ttl_and_no_key_per_place():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    asyncio.run(redis.sadd(keys.subscription(keys.POSTCODE), "0150", "9008", "SW1A 1AA"))
    answer_both()
    provider = MetAlertsProvider(requests.Session(), now=Clock())

    assert asyncio.run(ProviderRunner(provider, redis).run_cycle()) == 1

    assert asyncio.run(redis.keys("v1:notices:*")) == [keys.metalerts()]
    stored = StoredMetAlerts.model_validate_json(asyncio.run(redis.get(keys.metalerts())))
    assert stored == StoredMetAlerts.model_validate(FIXTURE)
    assert 0 < asyncio.run(redis.ttl(keys.metalerts())) <= provider.ttl_s
