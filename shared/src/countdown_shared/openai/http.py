"""What every call to OpenAI's API shares: where it is, the session that carries the key, and how a failure is named.

The key goes in a header, never a URL. A failure is named by OpenAI's error code alone (error_code): its message can
quote what was sent, and a prompt can name a screen's place.
"""

import requests

OPENAI_URL = "https://api.openai.com/v1"


def authorized(api_key: str, session: requests.Session | None = None) -> requests.Session:
    """Return `session` (a new one by default) sending `api_key` as its bearer token.

    A plain session, with no retries: a call that failed may have been paid for, so whether to ask again is the
    caller's to decide.
    """
    session = session or requests.Session()
    session.headers["Authorization"] = f"Bearer {api_key}"
    return session


def error_code(response: requests.Response) -> str:
    """Return OpenAI's error code (moderation_blocked...), not its message, which can quote what was sent."""
    try:
        error = response.json()["error"]
        return str(error.get("code") or error.get("type") or "no code")
    except ValueError, KeyError, TypeError, AttributeError:
        return "no code"
