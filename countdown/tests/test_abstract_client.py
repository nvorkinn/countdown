import asyncio
import logging
import threading
from datetime import datetime, timedelta

import pytest
import requests
from test_utils import REGISTRATION

from countdown_api.models import Tfl
from countdown_core.core import abstract_client
from countdown_core.core.abstract_client import AbstractClient, ClientStatus, NotJSONError, json_content


class FakeClient(AbstractClient):
    poll_interval = timedelta(minutes=5)

    def __init__(self, config=None, initialise_failures: int = 0):
        super().__init__(REGISTRATION, config)
        self.initialise_failures = initialise_failures
        self.initialise_calls = 0
        self.update_calls = 0

    def _initialise(self) -> None:
        self.initialise_calls += 1
        if self.initialise_failures:
            self.initialise_failures -= 1
            msg = "API down"
            raise ConnectionError(msg)

    def _update(self):
        self.update_calls += 1
        return "panel"


def test_is_due_before_the_first_update():
    assert FakeClient().is_due


def test_update_records_when_it_ran_and_returns_the_panel():
    client = FakeClient()

    assert asyncio.run(client.update()) == "panel"

    assert client.last_updated is not None
    assert not client.is_due


def test_is_due_again_once_the_poll_interval_has_passed():
    client = FakeClient()
    client.last_updated = datetime.now() - timedelta(minutes=5, seconds=1)  # noqa: DTZ005 -- the client's clock is naive local
    assert client.is_due

    client.last_updated = datetime.now() - timedelta(minutes=4)  # noqa: DTZ005 -- the client's clock is naive local
    assert not client.is_due


def test_cache_and_compare_is_false_the_first_time_even_for_none():
    client = FakeClient()

    assert client._cache_and_compare("endpoint", None) is False
    assert client._cache_and_compare("endpoint", None) is True
    assert client._cache_and_compare("endpoint", "changed") is False


def test_needs_refresh_is_true_for_any_config_change_by_default():
    client = FakeClient(Tfl(app_key="", stop_ids=["a"]))

    assert not client.needs_refresh(Tfl(app_key="", stop_ids=["a"]))
    assert client.needs_refresh(Tfl(app_key="", stop_ids=["a", "b"]))
    assert client.needs_refresh(Tfl(app_key="new", stop_ids=["a"]))


def test_initialise_marks_the_client_connected():
    client = FakeClient()
    assert client.status == ClientStatus.UNINITIALISED

    asyncio.run(client.initialise())

    assert client.status == ClientStatus.CONNECTED


def test_failed_initialise_marks_the_client_errored_and_raises():
    client = FakeClient(initialise_failures=1)

    with pytest.raises(ConnectionError):
        asyncio.run(client.initialise())

    assert client.status == ClientStatus.ERROR


def test_update_initialises_a_client_that_never_was_then_not_again():
    client = FakeClient()

    asyncio.run(client.update())
    asyncio.run(client.update())

    assert (client.initialise_calls, client.update_calls) == (1, 2)


def test_update_retries_initialisation_after_an_earlier_failure():
    client = FakeClient(initialise_failures=1)
    with pytest.raises(ConnectionError):
        asyncio.run(client.initialise())  # e.g. the API was down at boot

    assert asyncio.run(client.update()) == "panel"

    assert client.status == ClientStatus.CONNECTED
    assert client.initialise_calls == 2


def test_failed_reinitialisation_raises_skips_the_update_and_waits_a_full_poll_interval():
    client = FakeClient(initialise_failures=1)

    with pytest.raises(ConnectionError):
        asyncio.run(client.update())

    assert client.status == ClientStatus.ERROR
    assert client.update_calls == 0
    assert not client.is_due  # not retried every cycle -- some APIs rate-limit repeated failures


def test_a_failing_update_does_not_trigger_reinitialisation():
    """Only a client that never got connected re-initialises.

    A connected one that has a bad poll (a blip, a 500) just tries again next time.
    """
    client = FakeClient()
    asyncio.run(client.update())

    def failing_update():
        msg = "blip"
        raise ConnectionError(msg)

    client._update = failing_update
    with pytest.raises(ConnectionError):
        asyncio.run(client.update())

    assert client.status == ClientStatus.CONNECTED


def test_disabled_client_stays_disabled_and_makes_no_calls():
    client = FakeClient()
    client.status = ClientStatus.DISABLED

    asyncio.run(client.initialise())
    assert asyncio.run(client.update()) is None

    assert client.status == ClientStatus.DISABLED
    assert (client.initialise_calls, client.update_calls) == (0, 0)


def test_the_hooks_run_in_a_worker_thread_not_on_the_event_loop():
    """The clients block on `requests`; on the loop thread that would freeze everything."""
    seen: dict[str, int] = {}

    class Recording(FakeClient):
        def _initialise(self):
            seen["initialise"] = threading.get_ident()

        def _update(self):
            seen["update"] = threading.get_ident()

    async def run():
        loop_thread = threading.get_ident()
        await Recording().update()
        return loop_thread

    loop_thread = asyncio.run(run())

    assert seen["initialise"] != loop_thread
    assert seen["update"] != loop_thread


def test_several_clients_are_really_polled_concurrently():
    """Both hooks wait at a barrier that only opens if they run at the same time.

    Run one after the other, the first would time out.
    """
    barrier = threading.Barrier(2, timeout=5)

    class Waits(FakeClient):
        def _update(self):
            barrier.wait()
            return "panel"

    async def run():
        return await asyncio.gather(Waits().update(), Waits().update())

    assert asyncio.run(run()) == ["panel", "panel"]


def test_an_exception_in_a_hook_still_reaches_the_caller_from_its_thread():
    with pytest.raises(ConnectionError, match="API down"):
        asyncio.run(FakeClient(initialise_failures=1).initialise())


def test_a_slow_call_is_logged_by_client_name_and_a_fast_one_is_not(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    client = FakeClient()

    monkeypatch.setattr(abstract_client, "SLOW_CALL_S", 60)
    asyncio.run(client.update())
    assert caplog.text == ""

    monkeypatch.setattr(abstract_client, "SLOW_CALL_S", 0)
    asyncio.run(client.update())
    assert "FakeClient: update took" in caplog.text


def _response(content_type: str | None, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = "https://broker.example.com/api/thing"
    response._content = b"{}"
    if content_type is not None:
        response.headers["Content-Type"] = content_type
    return response


@pytest.mark.parametrize(
    "content_type", ["application/json", "application/json; charset=utf-8", "application/problem+json"]
)
def test_json_content_returns_a_json_body(content_type):
    assert json_content(_response(content_type)) == b"{}"


@pytest.mark.parametrize(
    ("content_type", "named"), [("text/html; charset=utf-8", "text/html"), (None, "no content type")]
)
def test_json_content_names_what_came_back_instead_of_json(content_type, named):
    with pytest.raises(
        NotJSONError, match=rf"^https://broker.example.com/api/thing answered with {named} \(200\), not JSON$"
    ):
        json_content(_response(content_type))


def test_json_content_raises_for_an_error_status_first():
    with pytest.raises(requests.HTTPError):
        json_content(_response("text/html", status=502))
