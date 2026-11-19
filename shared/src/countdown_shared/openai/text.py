"""OpenAI's text model, asked for an answer in a JSON shape of the caller's (the Responses API's structured output).

One question, one answer: instructions, the text they are about, and the schema the answer must have. A failure of
any kind is a TextModelError with nothing of the request in its text but the model's status and error code: the key
goes in a header, and the text asked about is the caller's to keep out of the logs. A refusal is its own error
(TextRefusedError), as asking again would be refused again.

Each call is logged (countdown_shared.openai.http.post), and so is what the model answered, word for word: its text
or its refusal. So the answer mustn't be asked to say where a screen is, as the logs may not (countdown_shared.core.
log_format).
"""

import json
import logging
import os
from typing import Any

import requests

from countdown_shared.openai.http import OPENAI_URL, authorized, error_code, post

logger = logging.getLogger(__name__)

RESPONSES_URL = f"{OPENAI_URL}/responses"
# A short answer takes a second or two; a provider's cycle waits on it.
OPENAI_TIMEOUT_S = 15
# Room for the answer and for whatever the model thinks first: an answer cut short is no answer.
MAX_OUTPUT_TOKENS = 1000


class TextModelError(Exception):
    """The model gave no answer: it failed, timed out, couldn't be reached, or answered in another shape."""


class TextRefusedError(TextModelError):
    """The model refused the text, and would again."""


class OpenAIText:
    """OpenAI's Responses API, for an answer in a JSON schema."""

    def __init__(self, api_key: str, model: str, session: requests.Session | None = None) -> None:
        """`model` is OPENAI_TEXT_MODEL's."""
        self.name = model
        self._session = authorized(api_key, session)

    def answer(self, instructions: str, text: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the model's answer about `text`, as the object `schema` describes. Raises TextModelError.

        `text` is sent as a JSON string in a message of its own, apart from the instructions, so the instructions
        can call it data. The schema is strict: every property required, no others. The answer isn't kept by OpenAI.
        """
        body = {
            "model": self.name,
            "input": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(text, ensure_ascii=False)},
            ],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        }
        try:
            response = post(self._session, RESPONSES_URL, self.name, json=body, timeout=OPENAI_TIMEOUT_S)
        except requests.RequestException as error:
            msg = f"OpenAI couldn't be reached ({type(error).__name__})"
            raise TextModelError(msg) from None
        if not response.ok:
            msg = f"OpenAI answered {response.status_code}: {error_code(response)}"
            raise TextModelError(msg)
        return _answer(response, self.name)


def _answer(response: requests.Response, model: str) -> dict[str, Any]:
    """Return the object in the answer's output text, logging the text (or refusal) as `model` gave it.

    Raises TextRefusedError for a refusal, TextModelError else.
    """
    try:
        answered = response.json()
        if answered.get("status") == "incomplete":
            reason = (answered.get("incomplete_details") or {}).get("reason") or "no reason"
            msg = f"OpenAI's answer was cut short ({reason})"
            raise TextModelError(msg)
        parts = [part for item in answered["output"] if item.get("type") == "message" for part in item["content"]]
        for part in parts:
            if part.get("type") == "refusal":
                logger.info("%s refused: %s", model, part.get("refusal"))
            elif part.get("type") == "output_text":
                logger.info("%s answered: %s", model, part.get("text"))
        if any(part.get("type") == "refusal" for part in parts):
            msg = "OpenAI refused the text"
            raise TextRefusedError(msg)
        value = json.loads(next(part["text"] for part in parts if part.get("type") == "output_text"))
    except ValueError, KeyError, TypeError, AttributeError, StopIteration:
        msg = "OpenAI's answer had no text in the shape asked for"
        raise TextModelError(msg) from None
    if not isinstance(value, dict):
        msg = "OpenAI's answer had no text in the shape asked for"
        raise TextModelError(msg)
    return value


# The setting (a service's environment) and its default: OpenAI's most efficient model, which is plenty for a line.
DEFAULT_OPENAI_TEXT_MODEL = "gpt-6-luna"


def configured() -> OpenAIText | None:
    """Return OpenAI's text model, if the environment has its key (OPENAI_API_KEY); None without."""
    if key := os.environ.get("OPENAI_API_KEY"):
        return OpenAIText(key, os.environ.get("OPENAI_TEXT_MODEL") or DEFAULT_OPENAI_TEXT_MODEL)
    return None
