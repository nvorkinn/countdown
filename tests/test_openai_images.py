"""The image model (countdown_shared.openai.images): OpenAI's API, faked with `responses`."""

import base64
import json

import pytest
import responses

from countdown_shared.openai import images
from countdown_shared.openai.images import IMAGES_URL, ImageModelError, OpenAIImages

JPEG = b"\xff\xd8\xff\xe0 a picture"


def _body(call) -> bytes:
    """Return a faked call's request body, which `responses` types loosely, as the bytes or text it is."""
    body = call.request.body
    assert isinstance(body, bytes | str)
    return body.encode() if isinstance(body, str) else body


def _openai_answer():
    return {"data": [{"b64_json": base64.b64encode(JPEG).decode()}]}


@responses.activate
def test_openai_makes_a_landscape_jpeg_with_the_key():
    call = responses.post(f"{IMAGES_URL}/generations", json=_openai_answer())

    picture = OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt")

    assert picture == JPEG
    sent = json.loads(_body(call.calls[0]))
    assert sent | {"prompt": "a prompt"} == sent
    assert (sent["model"], sent["size"], sent["quality"], sent["output_format"]) == (
        "gpt-image-2",
        "1280x800",
        "high",
        "jpeg",
    )
    assert call.calls[0].request.headers["Authorization"] == "Bearer sk-test"


@responses.activate
def test_openai_relights_a_reference_with_an_edit():
    call = responses.post(f"{IMAGES_URL}/edits", json=_openai_answer())

    picture = OpenAIImages("sk-test", "gpt-image-2", "high").make("relight", JPEG)

    assert picture == JPEG
    body = _body(call.calls[0])
    assert b'name="image[]"' in body
    assert JPEG in body


@responses.activate
def test_an_openai_refusal_names_its_code_not_the_prompt():
    responses.post(
        f"{IMAGES_URL}/generations",
        status=400,
        json={"error": {"code": "moderation_blocked", "message": "the prompt about Somewhere"}},
    )

    with pytest.raises(ImageModelError, match="400: moderation_blocked") as error:
        OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt about Somewhere")
    assert "Somewhere" not in str(error.value)


def test_the_configured_model_is_openais_when_it_has_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert images.configured() == []

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
    assert [model.name for model in images.configured()] == ["gpt-image-2.5-sunburst"]


@responses.activate
def test_an_answer_with_no_picture_or_no_code_says_so():
    responses.post(f"{IMAGES_URL}/generations", json={"data": []})
    with pytest.raises(ImageModelError, match="no picture"):
        OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt")

    responses.post(f"{IMAGES_URL}/edits", status=500, body="not json")
    with pytest.raises(ImageModelError, match="500: no code"):
        OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt", JPEG)


@responses.activate
def test_an_openai_that_cant_be_reached_names_the_failure_not_the_url():
    # Nothing registered: `responses` raises a ConnectionError, whose text quotes the URL.
    with pytest.raises(ImageModelError, match=r"couldn't be reached \(ConnectionError\)") as error:
        OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt")
    assert "api.openai.com" not in str(error.value)


@responses.activate
def test_openai_logs_each_call_without_its_prompt(caplog):
    responses.post(f"{IMAGES_URL}/generations", json=_openai_answer())
    responses.post(f"{IMAGES_URL}/edits", json=_openai_answer())
    model = OpenAIImages("sk-test", "gpt-image-2", "high")

    with caplog.at_level("INFO", logger="countdown_shared.openai"):
        model.make("Kennington at dusk")
        model.make("Kennington at dusk", JPEG)

    made, relit = caplog.messages
    assert made.startswith("Called OpenAI's images/generations with gpt-image-2: answered 200 in ")
    assert relit.startswith("Called OpenAI's images/edits with gpt-image-2: answered 200 in ")
    assert "Kennington" not in caplog.text
