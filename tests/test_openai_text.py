"""The text model (countdown_shared.openai.text): OpenAI's Responses API, faked with `responses`."""

import json

import pytest
import responses

from countdown_shared.openai import text
from countdown_shared.openai.text import RESPONSES_URL, OpenAIText, TextModelError, TextRefusedError

SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}
NOTICE = 'Severe delays "between" Kennington and Morden.\nSee https://tfl.gov.uk'


def _answer(*parts, status="completed", **more):
    return {"status": status, "output": [{"type": "reasoning"}, {"type": "message", "content": list(parts)}], **more}


def _text(value):
    return {"type": "output_text", "text": json.dumps(value)}


def _ask():
    return OpenAIText("sk-test", "gpt-6-luna").answer("Shorten it.", NOTICE, "short_notice", SCHEMA)


@responses.activate
def test_it_asks_for_a_strict_json_answer_about_the_text_as_data_with_the_key():
    call = responses.post(RESPONSES_URL, json=_answer(_text({"title": "Severe delays"})))

    assert _ask() == {"title": "Severe delays"}

    request = call.calls[0].request
    sent = json.loads(request.body)
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert (sent["model"], sent["store"]) == ("gpt-6-luna", False)
    assert sent["input"][0] == {"role": "system", "content": "Shorten it."}
    # The text is a message of its own, as one JSON string: quotes, line breaks and all.
    assert sent["input"][1]["role"] == "user"
    assert json.loads(sent["input"][1]["content"]) == NOTICE
    assert sent["text"]["format"] == {"type": "json_schema", "name": "short_notice", "strict": True, "schema": SCHEMA}
    assert sent["max_output_tokens"] > 0


@responses.activate
def test_a_refusal_is_its_own_error():
    responses.post(RESPONSES_URL, json=_answer({"type": "refusal", "refusal": "I can't help with Kennington"}))

    with pytest.raises(TextRefusedError) as error:
        _ask()
    assert "Kennington" not in str(error.value)


@responses.activate
def test_an_answer_cut_short_says_why():
    responses.post(RESPONSES_URL, json=_answer(status="incomplete", incomplete_details={"reason": "max_output_tokens"}))

    with pytest.raises(TextModelError, match=r"cut short \(max_output_tokens\)") as error:
        _ask()
    assert not isinstance(error.value, TextRefusedError)


@pytest.mark.parametrize(
    "body",
    [
        {"status": "completed", "output": []},
        {"status": "completed"},
        _answer({"type": "output_text", "text": "not json"}),
        _answer({"type": "output_text", "text": '["a list"]'}),
        ["not", "an", "object"],
    ],
)
@responses.activate
def test_an_answer_in_another_shape_is_no_answer(body):
    responses.post(RESPONSES_URL, json=body)

    with pytest.raises(TextModelError, match="no text in the shape asked for"):
        _ask()


@responses.activate
def test_a_failure_names_its_status_and_code_not_what_was_sent():
    responses.post(
        RESPONSES_URL,
        status=429,
        json={"error": {"code": "rate_limit_exceeded", "message": "Too many requests about Kennington"}},
    )

    with pytest.raises(TextModelError, match="429: rate_limit_exceeded") as error:
        _ask()
    assert "Kennington" not in str(error.value)


@responses.activate
def test_an_openai_that_cant_be_reached_names_the_failure_not_the_url():
    with pytest.raises(TextModelError, match=r"couldn't be reached \(ConnectionError\)") as error:
        _ask()
    assert "api.openai.com" not in str(error.value)


def test_the_configured_model_is_openais_when_it_has_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_TEXT_MODEL", raising=False)
    assert text.configured() is None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model = text.configured()
    assert model is not None
    assert model.name == text.DEFAULT_OPENAI_TEXT_MODEL
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "another")
    model = text.configured()
    assert model is not None
    assert model.name == "another"


@responses.activate
def test_it_logs_the_call_with_what_was_sent_and_answered_but_not_the_key(caplog):
    responses.post(RESPONSES_URL, json=_answer(_text({"title": "Severe delays"})))

    with caplog.at_level("INFO", logger="countdown_shared.openai"):
        _ask()

    call, sent, answered = caplog.messages
    assert call.startswith("Called OpenAI's responses: answered 200 in ")
    assert sent.startswith("Sent to OpenAI's responses: {")
    assert "Shorten it." in sent
    assert "Kennington and Morden" in sent
    assert answered.startswith("OpenAI's responses answered: {")
    assert "Severe delays" in answered
    assert "sk-test" not in caplog.text


@responses.activate
def test_it_logs_a_refusal_and_a_failure_as_openai_answered_them(caplog):
    responses.post(RESPONSES_URL, json=_answer({"type": "refusal", "refusal": "I can't help with that"}))
    responses.post(RESPONSES_URL, status=429, json={"error": {"code": "rate_limit_exceeded", "message": "Slow down"}})

    with caplog.at_level("INFO", logger="countdown_shared.openai"):
        with pytest.raises(TextRefusedError):
            _ask()
        with pytest.raises(TextModelError):
            _ask()

    assert "I can't help with that" in caplog.messages[2]
    assert caplog.messages[3].startswith("Called OpenAI's responses: answered 429 (rate_limit_exceeded) in ")
    assert "Slow down" in caplog.messages[5]
