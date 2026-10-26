"""The requests session the clients and providers share: retries connection errors and 502/503/504 with backoff."""

import requests
from requests.adapters import HTTPAdapter
from urllib3 import Retry

DEFAULT_TIMEOUT = 10


def retrying_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    """Build a requests.Session that retries connection errors and 502/503/504 with backoff."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[502, 503, 504],
        allowed_methods=None,  # retry on every method, including the POST used for auth
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
