"""The background provider (countdown_providers.background.main): what it makes, when, and what it writes to the store.

Through the runner, as it runs: against fakeredis, with a fake image model. The runner's client decodes, the pictures'
doesn't, and both are the one store.
"""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from countdown_api import fixtures
from countdown_api.models import BackgroundPlace, StoredBackground
from countdown_providers import runner as runner_module
from countdown_providers.background import logic
from countdown_providers.background import main as provider_module
from countdown_providers.background.main import BackgroundProvider
from countdown_providers.config import Settings
from countdown_providers.runner import ProviderRunner
from countdown_shared.openai.images import ImageModelError
from countdown_shared.store import keys

WEATHER = fixtures.load("weather.json")
KEY = WEATHER["weather_key"]
PLACE = {"name": "London, England, United Kingdom", "country": "GB", "postcodes": ["SE17 3LL"]}
# The example's first day: dawn is due from 06:43, the day's picture from 08:43.
DAWN = datetime(2026, 1, 15, 7, tzinfo=UTC)
NOON = datetime(2026, 1, 15, 12, tzinfo=UTC)


class Model:
    """A fake image model: answers a JPEG of its own per call, or fails."""

    def __init__(self, name="gpt-image-2", *, fails=False):
        self.name = name
        self.fails = fails
        self.calls: list[tuple[str, bytes | None]] = []

    def make(self, prompt, reference=None):
        self.calls.append((prompt, reference))
        if self.fails:
            msg = "OpenAI answered 500: server_error"
            raise ImageModelError(msg)
        return b"\xff\xd8\xff" + f"{self.name} {len(self.calls)}".encode()


class Store:
    """One fake store, as the provider's two clients and as the tests read it (synchronously, in bytes)."""

    def __init__(self):
        server = fakeredis.FakeServer()
        self.redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        self.pictures = fakeredis.FakeAsyncRedis(server=server)
        self.sync = fakeredis.FakeRedis(server=server)

    def background(self, weather_key=KEY) -> dict:
        value = self.sync.get(keys.background(weather_key))
        assert value is not None
        StoredBackground.model_validate_json(value)
        return json.loads(value)


@pytest.fixture
def store():
    """Return a store with the example's forecast, and its place subscribed as a touchscreen's."""
    store = Store()
    store.sync.set(keys.weather(KEY), json.dumps(WEATHER["value"]))
    store.sync.sadd(keys.subscription(keys.BACKGROUND), KEY)
    store.sync.set(keys.background_place(KEY), json.dumps(PLACE))
    return store


class Run:
    """The provider and its runner, at a time the test moves."""

    def __init__(self, store, models, now):
        self.now = now
        self.provider = BackgroundProvider(store.redis, store.pictures, models, now=lambda: self.now)
        self.runner = ProviderRunner(self.provider, store.redis)

    def cycle(self) -> int:
        return asyncio.run(self.runner.run_cycle())


def test_the_example_weather_is_under_its_key_and_the_place_is_the_contracts():
    assert WEATHER["key"] == keys.weather(KEY)
    BackgroundPlace.model_validate(PLACE)
    BackgroundPlace.model_validate(fixtures.load("background_place.json")["value"])


def test_dawn_is_made_fresh_and_stored_by_its_digest(store):
    model = Model()

    assert Run(store, [model], DAWN).cycle() == 1

    stored = store.background()
    assert (stored["location"], stored["local_date"]) == (KEY, "2026-01-15")
    [image] = stored["images"]
    assert (image["phase"], image["fallback"]) == ("dawn", False)
    assert datetime.fromisoformat(image["made_at"]) == DAWN
    picture = store.sync.get(keys.background_image(image["digest"]))
    assert hashlib.sha256(picture).hexdigest() == image["digest"]
    assert model.calls[0][1] is None
    assert "at sunrise" in model.calls[0][0] or "first light" in model.calls[0][0]
    assert 0 < store.sync.ttl(keys.background(KEY)) <= keys.BACKGROUND_TTL_S
    assert 0 < store.sync.ttl(keys.background_image(image["digest"])) <= keys.BACKGROUND_IMAGE_TTL_S


def test_what_is_written_reads_as_the_contracts_example_does(store):
    """Its times are the contract's date-times, whichever way their offset is written."""
    run = Run(store, [Model()], DAWN)
    run.cycle()

    [image] = StoredBackground.model_validate_json(store.sync.get(keys.background(KEY))).images
    example = StoredBackground.model_validate(fixtures.load("background.json")["value"]).images[0]
    assert (image.phase, image.starts_at.utcoffset()) == (example.phase, example.starts_at.utcoffset())
    assert image.starts_at.second == 0


def test_the_picture_is_in_the_store_before_the_list_that_names_it(store, monkeypatch):
    seen = []
    write = store.pictures.set

    async def set_and_look(key, value, **kwargs):
        await write(key, value, **kwargs)
        seen.append(store.sync.exists(keys.background(KEY)))

    monkeypatch.setattr(store.pictures, "set", set_and_look)

    Run(store, [Model()], DAWN).cycle()

    # When the picture went in, the list wasn't there yet: the runner writes it after.
    assert seen == [0]
    assert store.sync.exists(keys.background(KEY)) == 1


def test_nothing_is_made_twice_or_before_its_time(store):
    model = Model()
    run = Run(store, [model], DAWN - timedelta(hours=2))

    assert run.cycle() == 0
    run.now = DAWN
    assert run.cycle() == 1
    assert run.cycle() == 0
    assert len(model.calls) == 1


def test_a_place_with_nothing_due_keeps_its_days_ttl(store):
    run = Run(store, [Model()], DAWN)
    run.cycle()
    store.sync.expire(keys.background(KEY), 100)

    assert run.cycle() == 0

    # Not written again, so not renewed: the TTL is renewed with each picture, as the contract says.
    assert store.sync.ttl(keys.background(KEY)) <= 100


def test_the_day_relights_dawn(store):
    model = Model()
    run = Run(store, [model], DAWN)
    run.cycle()
    dawn = store.sync.get(keys.background_image(store.background()["images"][0]["digest"]))

    run.now = NOON
    assert run.cycle() == 1

    prompt, reference = model.calls[1]
    assert reference == dawn
    assert prompt.startswith(logic.RELIGHT)
    assert [image["phase"] for image in store.background()["images"]] == ["dawn", "day"]


def test_a_failed_model_falls_back_to_the_curated_picture(store):
    Run(store, [Model(fails=True)], DAWN).cycle()

    [image] = store.background()["images"]
    assert image["fallback"] is True
    assert store.sync.get(keys.background_image(image["digest"])) == logic.fallback("dawn")


def test_the_next_model_is_tried_when_the_first_fails(store):
    first, second = Model("first", fails=True), Model("second")

    Run(store, [first, second], DAWN).cycle()

    [image] = store.background()["images"]
    assert image["fallback"] is False
    assert store.sync.get(keys.background_image(image["digest"])) == b"\xff\xd8\xffsecond 1"


def test_without_a_key_the_pictures_are_the_curated_ones(store):
    Run(store, [], DAWN).cycle()

    assert store.background()["images"][0]["fallback"] is True


def test_a_fallback_is_tried_again_after_half_an_hour(store):
    model = Model(fails=True)
    run = Run(store, [model], DAWN)
    run.cycle()

    model.fails = False
    run.now = DAWN + timedelta(minutes=10)
    assert run.cycle() == 0
    run.now = DAWN + timedelta(minutes=30)
    assert run.cycle() == 1

    [image] = store.background()["images"]
    assert image["fallback"] is False


def test_a_new_day_starts_a_new_list_at_dawn(store):
    store.sync.set(
        keys.background(KEY),
        json.dumps(
            {
                "location": KEY,
                "local_date": "2026-01-14",
                "images": [
                    {
                        "phase": "night",
                        "starts_at": "2026-01-14T17:05:00+00:00",
                        "digest": "a" * 64,
                        "made_at": "2026-01-14T16:20:00Z",
                        "fallback": False,
                    },
                ],
            }
        ),
    )

    Run(store, [Model()], DAWN).cycle()

    stored = store.background()
    assert stored["local_date"] == "2026-01-15"
    assert [image["phase"] for image in stored["images"]] == ["dawn"]


def test_no_forecast_no_background(store):
    store.sync.delete(keys.weather(KEY))
    model = Model()

    assert Run(store, [model], DAWN).cycle() == 0
    assert model.calls == []


def test_a_subscribed_place_the_broker_hasnt_described_waits(store):
    store.sync.delete(keys.background_place(KEY))
    model = Model()

    assert Run(store, [model], DAWN).cycle() == 0
    assert model.calls == []


def test_a_place_that_isnt_the_contracts_waits_and_is_logged_without_its_key(store, caplog):
    store.sync.set(keys.background_place(KEY), json.dumps({"name": "London"}))
    model = Model()

    with caplog.at_level(logging.DEBUG):
        assert Run(store, [model], DAWN).cycle() == 0

    assert model.calls == []
    assert "isn't the contract's BackgroundPlace" in caplog.text
    assert KEY not in caplog.text


def test_only_the_subscribed_places_are_made(store):
    """A place in v1:sub:weather alone (an e-paper screen's) is none of this provider's."""
    other = "oslo-no-3143244"
    store.sync.set(keys.weather(other), json.dumps(WEATHER["value"] | {"location": other}))
    store.sync.set(keys.background_place(other), json.dumps({"name": "Oslo", "country": "NO", "postcodes": []}))
    store.sync.sadd(keys.subscription(keys.WEATHER), KEY, other)

    assert Run(store, [Model()], DAWN).cycle() == 1

    assert store.sync.exists(keys.background(other)) == 0


def test_each_place_is_a_batch_of_its_own_written_as_its_made(store):
    other = "oslo-no-3143244"
    store.sync.set(keys.weather(other), json.dumps(WEATHER["value"] | {"location": other}))
    store.sync.set(keys.background_place(other), json.dumps({"name": "Oslo", "country": "NO", "postcodes": []}))
    store.sync.sadd(keys.subscription(keys.BACKGROUND), other)
    model = Model()
    run = Run(store, [model], DAWN)

    assert run.runner.batch_size == 1
    assert run.cycle() == 2

    assert store.background(other)["location"] == other
    assert len(model.calls) == 2
    # A place with no scenes of its own is pictured by the name the broker gave it.
    assert sum("everyday view of Oslo" in prompt for prompt, _ in model.calls) == 1


def test_the_prompt_has_the_places_warnings(store):
    store.sync.set(
        keys.met_office("SE17 3LL"),
        json.dumps(
            {
                "fetched_at": "2026-01-15T06:00:00Z",
                "postcode": "SE17 3LL",
                "region": "se",
                "items": [{"title": "Yellow warning of ice affecting London & South East"}],
            }
        ),
    )
    model = Model()

    Run(store, [model], DAWN).cycle()

    assert "yellow warning of ice" in model.calls[0][0]


def test_a_place_outside_britain_reads_no_british_warnings(store):
    store.sync.set(keys.background_place(KEY), json.dumps(PLACE | {"country": "NO"}))
    store.sync.set(
        keys.met_office("SE17 3LL"),
        json.dumps(
            {
                "fetched_at": "2026-01-15T06:00:00Z",
                "postcode": "SE17 3LL",
                "region": "se",
                "items": [{"title": "Yellow warning of ice affecting London & South East"}],
            }
        ),
    )
    model = Model()

    Run(store, [model], DAWN).cycle()

    assert "warning of ice" not in model.calls[0][0]


def test_nothing_logged_names_the_place(store, caplog):
    """Not its weather key, its name or its postcode: made, relit, failed and fallen back alike."""
    model = Model()
    run = Run(store, [model], DAWN)
    with caplog.at_level(logging.DEBUG):
        run.cycle()
        model.fails = True
        run.now = NOON
        run.cycle()

    assert "Made a dawn background" in caplog.text
    assert "gpt-image-2 made no day background: OpenAI answered 500: server_error" in caplog.text
    assert "Made a day background (a fallback)" in caplog.text
    for record in caplog.records:
        for secret in (KEY, "london", "London", "SE17", "2643743"):
            assert secret not in record.getMessage()


def test_it_outlives_many_intervals_and_writes_under_the_contracts_keys(store):
    provider = BackgroundProvider(store.redis, store.pictures, [])
    assert provider.name == "background"
    assert provider.ttl_s == keys.BACKGROUND_TTL_S >= 3 * provider.interval_s
    assert provider.output_key(KEY) == fixtures.load("background.json")["key"]


def test_the_process_runs_it_on_two_clients_and_starts_without_a_key(monkeypatch, caplog):
    """Through main(), as the container starts it: no OPENAI_API_KEY, so the curated pictures, and it says so."""
    ran = []
    closed = []

    class FakeRunner:
        def __init__(self, provider, redis, *, shard):
            self.provider = provider
            self.redis = redis

        async def run(self):
            ran.append(self)

        def stop(self):
            pass

    def fake_redis(self, *, decode_responses=True):
        client = fakeredis.FakeAsyncRedis(decode_responses=decode_responses)

        async def aclose():
            closed.append(decode_responses)

        client.aclose = aclose
        return client

    monkeypatch.setattr(runner_module, "ProviderRunner", FakeRunner)
    monkeypatch.setattr(provider_module, "configure_logging", lambda: None)
    monkeypatch.setattr(Settings, "redis", fake_redis)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with caplog.at_level(logging.WARNING):
        provider_module.main()

    [runner] = ran
    assert isinstance(runner.provider, BackgroundProvider)
    assert runner.provider.models == []
    assert runner.provider.redis is runner.redis
    assert runner.provider.pictures is not runner.redis
    # Both closed as it stops: the runner's, and the pictures'.
    assert sorted(closed) == [False, True]
    assert "No OPENAI_API_KEY" in caplog.text


def test_with_a_key_the_model_is_openais(monkeypatch):
    made = []
    monkeypatch.setattr(provider_module, "configure_logging", lambda: None)
    monkeypatch.setattr(provider_module, "_serve", lambda settings, models: made.append(models) or asyncio.sleep(0))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2")

    provider_module.main()

    assert [model.name for model in made[0]] == ["gpt-image-2"]
