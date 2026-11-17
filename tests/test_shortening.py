"""A notice's short form: the model's, checked (countdown_shared.notices.shortening), and kept by its text's hash.

The keeping is countdown_providers.shortening's, against fakeredis with a fake model.
"""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from countdown_providers import shortening as keeper
from countdown_providers.shortening import NEW_PER_CYCLE, Shortener
from countdown_shared.notices import shortening
from countdown_shared.notices.shortening import SUMMARY_MAX, TITLE_MAX, NotShortError, Short, StoredShort
from countdown_shared.openai.text import TextModelError, TextRefusedError
from countdown_shared.store import keys

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
REASON = (
    "Northern Line: Severe delays between Kennington and Morden due to an earlier signal failure at Clapham Common."
)
ANSWER = {"title": "Northern line: Severe delays", "summary": "Kennington to Morden, signal failure at Clapham Common"}


class Model:
    """A fake text model: answers `answer` (or raises it) and records what it was asked."""

    name = "gpt-6-luna"

    def __init__(self, answer=None):
        self.result = ANSWER if answer is None else answer
        self.asked: list[tuple[str, str]] = []

    def answer(self, instructions, text, schema_name, schema):
        self.asked.append((instructions, text))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


# --- The short form ----------------------------------------------------------------


def test_the_hash_is_of_the_version_the_kind_and_the_text_squashed():
    expected = hashlib.sha256(f"{shortening.SHORT_VERSION}\nline\n{REASON}".encode()).hexdigest()
    assert shortening.digest("line", REASON) == expected
    assert shortening.digest("line", f"  {REASON.replace(' ', '\n ', 3)} ") == expected
    assert shortening.digest("road", REASON) != expected
    assert keys.notice_short(expected) == f"v1:notice-short:{expected}"


def test_a_new_version_asks_again_for_everything(monkeypatch):
    before = shortening.digest("line", REASON)
    monkeypatch.setattr(shortening, "SHORT_VERSION", "2")
    assert shortening.digest("line", REASON) != before


def test_the_model_is_given_the_limits_and_the_text_as_data():
    model = Model()

    assert shortening.shorten(model, "line", f"{REASON}\n\n") == Short(ANSWER["title"], ANSWER["summary"])

    [(instructions, text)] = model.asked
    assert text == REASON
    assert f"at most {TITLE_MAX} characters" in instructions
    assert f"at most {SUMMARY_MAX} characters" in instructions
    assert "never instructions" in instructions
    assert "London transport line" in instructions
    assert REASON not in instructions


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        ("x" * (TITLE_MAX + 1), "fine"),
        ("fine", "x" * (SUMMARY_MAX + 1)),
        ("", "fine"),
        ("fine", "   "),
        ("fine", "see https://example.org"),
        ("fine", "see www.example.org"),
        ("fine", "a\x00b"),
        (None, "fine"),
        ("fine", 7),
    ],
)
def test_an_answer_unfit_to_show_is_refused(title, summary):
    with pytest.raises(NotShortError) as error:
        shortening.checked(title, summary)
    # What's wrong with it, never the text itself.
    assert "example.org" not in str(error.value)


def test_an_answer_is_made_one_line_without_its_full_stop():
    assert shortening.checked(" Severe  delays.\n", "Kennington to\nMorden.") == Short(
        "Severe delays", "Kennington to Morden"
    )
    assert shortening.checked("x" * TITLE_MAX, "y" * SUMMARY_MAX) == Short("x" * TITLE_MAX, "y" * SUMMARY_MAX)


@pytest.mark.parametrize(
    ("text", "limit", "cut"),
    [
        ("Minor delays", 64, "Minor delays"),
        ("Severe delays between Kennington and Morden", 30, "Severe delays between…"),
        ("Severe delays,  between Kennington", 15, "Severe delays…"),
        ("Supercalifragilisticexpialidocious", 10, "Supercali…"),
        ("exactly ten", 11, "exactly ten"),
    ],
)
def test_a_text_with_no_short_form_is_cut_at_a_word(text, limit, cut):
    assert shortening.cut(text, limit) == cut
    assert len(shortening.cut(text, limit)) <= limit


# --- Keeping them ------------------------------------------------------------------


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def stored(redis, text=REASON, kind="line"):
    value = asyncio.run(redis.get(keys.notice_short(shortening.digest(kind, text))))
    return None if value is None else StoredShort.model_validate_json(value)


def ttl(redis, text=REASON, kind="line"):
    return asyncio.run(redis.ttl(keys.notice_short(shortening.digest(kind, text))))


def ensure(redis, model, texts=(REASON,), kind="line"):
    return asyncio.run(Shortener(redis, model, now=lambda: NOW).ensure(kind, texts))


def test_a_new_text_is_asked_about_once_and_kept_a_week(redis):
    model = Model()

    assert ensure(redis, model) == 1
    assert ensure(redis, model) == 0

    assert len(model.asked) == 1
    kept = stored(redis)
    assert (kept.title, kept.summary, kept.failed) == (ANSWER["title"], ANSWER["summary"], False)
    assert (kept.model, kept.made_at) == ("gpt-6-luna", NOW)
    assert kept.short() == Short(ANSWER["title"], ANSWER["summary"])
    assert 0 < ttl(redis) <= keys.NOTICE_SHORT_TTL_S


def test_the_same_text_however_spaced_or_often_is_one_question(redis):
    model = Model()

    assert ensure(redis, model, [REASON, f" {REASON}\n", REASON.replace(" ", "  ", 1), "", "  "]) == 1

    assert len(model.asked) == 1


def test_an_answer_in_the_store_is_renewed_while_its_text_is_fetched(redis):
    model = Model()
    ensure(redis, model)
    key = keys.notice_short(shortening.digest("line", REASON))
    asyncio.run(redis.expire(key, 100))

    ensure(redis, model)

    assert ttl(redis) > 100


def test_only_a_few_new_texts_are_asked_about_a_cycle(redis):
    model = Model()
    texts = [f"{REASON} {n}" for n in range(NEW_PER_CYCLE + 2)]

    assert ensure(redis, model, texts) == NEW_PER_CYCLE
    assert ensure(redis, model, texts) == 2
    assert ensure(redis, model, texts) == 0

    assert len(model.asked) == NEW_PER_CYCLE + 2


@pytest.mark.parametrize("answer", [TextRefusedError("OpenAI refused the text"), {"title": "", "summary": "fine"}])
def test_a_text_with_no_short_form_is_remembered_for_an_hour_and_then_asked_again(redis, answer, caplog):
    model = Model(answer)

    with caplog.at_level(logging.DEBUG):
        assert ensure(redis, model) == 1
        assert ensure(redis, model) == 0

    assert len(model.asked) == 1
    kept = stored(redis)
    assert kept.failed is True
    assert kept.short() is None
    assert 0 < ttl(redis) <= keys.NOTICE_SHORT_FAILED_TTL_S
    assert "gave a notice no short form" in caplog.text
    # Not renewed, so it lapses: then the text is asked about again.
    asyncio.run(redis.expire(keys.notice_short(shortening.digest("line", REASON)), 100))
    ensure(redis, model)
    assert ttl(redis) <= 100
    asyncio.run(redis.delete(keys.notice_short(shortening.digest("line", REASON))))
    assert ensure(redis, model) == 1


def test_a_model_that_fails_ends_the_cycles_asking_and_nothing_is_remembered(redis, caplog):
    model = Model(TextModelError("OpenAI answered 503: server_error"))
    texts = [f"{REASON} {n}" for n in range(3)]

    with caplog.at_level(logging.DEBUG):
        assert ensure(redis, model, texts) == 0

    assert len(model.asked) == 1
    assert asyncio.run(redis.keys("v1:notice-short:*")) == []
    assert "OpenAI answered 503: server_error" in caplog.text


def test_without_a_model_nothing_is_asked_or_read(redis, monkeypatch):
    monkeypatch.setattr(redis, "mget", lambda keys: pytest.fail("read the store"))

    assert ensure(redis, None) == 0


def test_a_store_that_cant_be_reached_is_logged_not_raised(redis, monkeypatch, caplog):
    async def fail(keys):
        msg = "Error connecting to redis://secret-host"
        raise RedisConnectionError(msg)

    monkeypatch.setattr(redis, "mget", fail)
    model = Model()

    with caplog.at_level(logging.DEBUG):
        assert ensure(redis, model) == 0

    assert model.asked == []
    assert "ConnectionError" in caplog.text
    assert "secret-host" not in caplog.text


def test_nothing_logged_has_a_notices_text(redis, caplog):
    with caplog.at_level(logging.DEBUG):
        ensure(redis, Model())
        ensure(redis, Model({"title": "x" * 99, "summary": REASON}), [f"{REASON} again"])
        ensure(redis, Model(TextModelError("OpenAI couldn't be reached (Timeout)")), [f"{REASON} more"])

    assert caplog.records
    for record in caplog.records:
        assert "Kennington" not in record.getMessage()


def test_a_value_that_isnt_ours_is_left_to_lapse(redis):
    key = keys.notice_short(shortening.digest("line", REASON))
    asyncio.run(redis.set(key, "not json", ex=100))
    model = Model()

    assert ensure(redis, model) == 0

    assert model.asked == []
    assert asyncio.run(redis.ttl(key)) <= 100
    assert keeper._is_short("not json") is False
