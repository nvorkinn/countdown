"""What every call to OpenAI's API shares: where it is, the session that carries the key, and how a call is logged.

The key goes in a header, never a URL. Every call is logged (post): which API, the model, and how it ended, but
nothing of what was sent, as a prompt can name a screen's place. A failure is named by OpenAI's error code alone
(error_code): its message can quote what was sent.
"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1"


def authorized(api_key: str, session: requests.Session | None = None) -> requests.Session:
    """Return `session` (a new one by default) sending `api_key` as its bearer token.

    A plain session, with no retries: a call that failed may have been paid for, so whether to ask again is the
    caller's to decide.
    """
    session = session or requests.Session()
    session.headers["Authorization"] = f"Bearer {api_key}"
    return session


def post(session: requests.Session, url: str, model: str, **request: Any) -> requests.Response:  # noqa: ANN401
    """Post `request` (requests' own arguments) to `url`, one of the API's, and log the call.

    The line names the API (responses, images/edits...), the model, and the status answered with its error code if
    it failed, or the failure's type if nothing answered, and how long it took. Raises requests.RequestException.
    """
    api = url.removeprefix(f"{OPENAI_URL}/")
    started = time.monotonic()
    try:
        response = session.post(url, **request)
    except requests.RequestException as error:
        took = time.monotonic() - started
        logger.info("Called OpenAI's %s with %s: %s after %.1f s", api, model, type(error).__name__, took)
        raise
    took = time.monotonic() - started
    outcome = str(response.status_code) if response.ok else f"{response.status_code} ({error_code(response)})"
    logger.info("Called OpenAI's %s with %s: answered %s in %.1f s", api, model, outcome, took)
    return response


def error_code(response: requests.Response) -> str:
    """Return OpenAI's error code (moderation_blocked...), not its message, which can quote what was sent."""
    try:
        error = response.json()["error"]
        return str(error.get("code") or error.get("type") or "no code")
    except ValueError, KeyError, TypeError, AttributeError:
        return "no code"
