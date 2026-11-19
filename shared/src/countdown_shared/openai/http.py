"""What every call to OpenAI's API shares: where it is, the session that carries the key, and how a call is logged.

The key goes in a header, never a URL, and no header is logged. Every call that is answered is logged by a hook on
the session (authorized): which API, the status and how long it took, and, for a caller that asks (`bodies`), what
was sent and what came back. A failure is named by OpenAI's error code alone (error_code): its message can quote what
was sent, and a prompt can name a screen's place.
"""

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1"


def authorized(api_key: str, session: requests.Session | None = None, *, bodies: bool = False) -> requests.Session:
    """Return `session` (a new one by default) sending `api_key` as its bearer token, and logging each call answered.

    A plain session, with no retries: a call that failed may have been paid for, so whether to ask again is the
    caller's to decide. `bodies` is for a caller whose requests and answers are text that says nothing of where a
    screen is: they are logged too, word for word. A call nothing answered (a timeout, no connection) raises with
    no response to log, so is its caller's to log.
    """
    session = session or requests.Session()
    session.headers["Authorization"] = f"Bearer {api_key}"
    session.hooks["response"].append(_logged(bodies=bodies))
    return session


def _logged(*, bodies: bool) -> Callable[..., None]:
    """Return a response hook that logs the call: the request is the response's own, so no header need be read."""

    def log(response: requests.Response, **_: Any) -> None:  # noqa: ANN401
        api = response.url.removeprefix(f"{OPENAI_URL}/")
        outcome = str(response.status_code) if response.ok else f"{response.status_code} ({error_code(response)})"
        logger.info("Called OpenAI's %s: answered %s in %.1f s", api, outcome, response.elapsed.total_seconds())
        if bodies:
            sent = response.request.body
            logger.info("Sent to OpenAI's %s: %s", api, sent.decode() if isinstance(sent, bytes) else sent)
            logger.info("OpenAI's %s answered: %s", api, response.text)

    return log


def error_code(response: requests.Response) -> str:
    """Return OpenAI's error code (moderation_blocked...), not its message, which can quote what was sent."""
    try:
        error = response.json()["error"]
        return str(error.get("code") or error.get("type") or "no code")
    except ValueError, KeyError, TypeError, AttributeError:
        return "no code"
