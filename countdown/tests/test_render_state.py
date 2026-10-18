"""A screen's render state (countdown_core.core.render_state): its shape, the handoff rule, and the in-memory store.

The rule is checked against what the renderer does now (DisplayController and ServerTarget), frame by frame, so a
render job that keeps its state in a store sends the broker exactly what a long-lived renderer would.
"""

import contextlib
import json
from datetime import UTC, datetime, timedelta

import pytest
import requests
import responses
from fake_target import FakeTarget
from pydantic import ValidationError
from test_display_controller import frame, show

from countdown_core.core.display import DisplayController
from countdown_core.core.render_state import (
    Handoff,
    InMemoryRenderStateStore,
    RenderState,
    decode,
    encode,
    frame_etag,
)
from countdown_core.core.targets import panel_bytes
from countdown_credentials.registration import RendererRegistration
from countdown_server.server_target import REFRESH_HINT_HEADER, ServerTarget

BROKER_URL = "https://broker.example.com"
FRAME_URL = f"{BROKER_URL}/api/frame"
AT = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
FRAME_BYTES = 800 * 480 // 8


# --- the frame's hash ---------------------------------------------------------------------------------------------


def test_the_hash_is_the_brokers_etag():
    # auth-broker's routes/frames.py: blake2b(frame, digest_size=8).hexdigest(), here for a blank and a black frame.
    assert frame_etag(bytes(FRAME_BYTES)) == "cf4d49a0c56195f9"
    assert frame_etag(b"\xff" * FRAME_BYTES) == "21404bb162874faf"


def test_different_frames_hash_differently():
    assert frame_etag(bytes(FRAME_BYTES)) != frame_etag(bytes(FRAME_BYTES - 1) + b"\x01")


# --- the handoff rule ---------------------------------------------------------------------------------------------


def test_the_first_frame_is_handed_off():
    assert RenderState().needs_put("a", None)
    assert RenderState().needs_put("a", 20)


def test_the_same_frame_again_is_not():
    state = RenderState().handed_off("a", None, AT)

    assert not state.needs_put("a", None)


def test_a_changed_frame_is():
    state = RenderState().handed_off("a", None, AT)

    assert state.needs_put("b", None)


def test_the_same_frame_is_sent_again_while_a_pace_is_asked_for():
    state = RenderState().handed_off("a", 20, AT)

    assert state.needs_put("a", 20)
    assert state.needs_put("a", 30)


def test_the_same_frame_is_sent_once_more_to_end_a_pace():
    state = RenderState().handed_off("a", 20, AT)

    assert state.needs_put("a", None)
    assert not state.handed_off("a", None, AT).needs_put("a", None)


def test_a_frame_the_broker_didnt_take_is_sent_again():
    # The state only moves once the broker has taken the frame, so one it refused is still due.
    state = RenderState().handed_off("a", None, AT)

    assert state.needs_put("b", None)
    assert state.needs_put("b", None)


def test_handing_off_records_what_was_sent():
    state = RenderState().handed_off("a", 20, AT)

    assert state.handoff == Handoff(etag="a", pace=20, at=AT)
    assert state.handed_off("b", None, AT + timedelta(minutes=1)).handoff == Handoff(
        etag="b", pace=None, at=AT + timedelta(minutes=1)
    )


def test_the_state_is_never_changed_in_place():
    state = RenderState()

    state.handed_off("a", None, AT)

    assert state.handoff is None
    with pytest.raises(ValidationError, match="frozen"):
        state.handoff = Handoff(etag="a", at=AT)


# --- the same as the renderer now ---------------------------------------------------------------------------------

# Each render: the panels drawn, the pace the providers ask for, and the broker's answer if it's sent the frame.
RENDERS = [
    (frame(), None, 204),  # the first picture
    (frame(), None, 204),  # the same: nothing to send
    (frame("gray"), None, 204),  # the arrivals changed
    (frame("gray"), 30, 204),  # a release starts: the same picture, sent again for its pace
    (frame("gray"), 30, 503),  # and again, but the broker can't take it
    (frame("gray"), 30, 204),  # so it's sent again
    (frame("white"), 30, 204),  # a new picture, with the pace
    (frame("white"), None, 204),  # the release is over: sent once more, to end the pace
    (frame("white"), None, 204),  # then nothing
    (frame(), None, 503),  # a new picture the broker can't take
    (frame(), None, 204),  # sent again
]


def _sent(calls) -> list[tuple[str, str | None]]:
    """Return each frame upload's hash and refresh hint."""
    return [(frame_etag(call.request.body), call.request.headers.get(REFRESH_HINT_HEADER)) for call in calls]


def test_the_rule_sends_the_broker_what_the_renderer_sends_it_now():
    registration = RendererRegistration(BROKER_URL, "device-secret", "device-123")

    # The renderer now: DisplayController decides whether the picture changed, ServerTarget renews the pace.
    with responses.RequestsMock(assert_all_requests_are_fired=False) as broker:
        display = DisplayController(ServerTarget(registration))
        for panels, pace, status in RENDERS:
            broker.put(FRAME_URL, status=status)
            display.set_pace(pace)
            with contextlib.suppress(requests.HTTPError):  # the loop logs it and carries on
                show(display, panels)
            broker.remove(responses.PUT, FRAME_URL)
        now = _sent(broker.calls)

    # A render job: the same frames, decided by their hashes and a state kept in a store between renders.
    store = InMemoryRenderStateStore()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as broker:
        target = ServerTarget(registration)
        for panels, pace, status in RENDERS:
            broker.put(FRAME_URL, status=status)
            # Drawn by a controller of its own each time, as a job would, with nothing remembered in it.
            image = show(DisplayController(FakeTarget()), panels)
            etag = frame_etag(bytes(panel_bytes(image)))
            state = store.load("device-123")
            if state.needs_put(etag, pace):
                target.set_pace(pace)
                try:
                    target.paint(image)
                except requests.HTTPError:
                    pass
                else:
                    store.save("device-123", state.handed_off(etag, pace, AT))
            broker.remove(responses.PUT, FRAME_URL)
        stored = _sent(broker.calls)

    assert stored == now
    assert len(now) == 9  # all but the two renders with nothing to send


# --- keeping it -------------------------------------------------------------------------------------------------


def test_a_screen_with_no_state_gets_an_empty_one():
    assert InMemoryRenderStateStore().load("device-123") == RenderState()


def test_a_saved_state_is_loaded_back():
    store = InMemoryRenderStateStore()
    state = RenderState().handed_off("a", 20, AT)

    store.save("device-123", state)

    assert store.load("device-123") == state


def test_saving_replaces_what_was_kept():
    store = InMemoryRenderStateStore()
    store.save("device-123", RenderState().handed_off("a", 20, AT))

    store.save("device-123", RenderState().handed_off("b", None, AT))

    assert store.load("device-123").handoff == Handoff(etag="b", at=AT)


def test_each_screen_has_its_own():
    store = InMemoryRenderStateStore()
    store.save("device-1", RenderState().handed_off("a", None, AT))

    assert store.load("device-2") == RenderState()
    assert store.load("device-1").handoff.etag == "a"


def test_it_is_kept_as_the_json_a_store_outside_the_process_would_hold():
    store = InMemoryRenderStateStore()
    store.save("device-123", RenderState().handed_off("a", 20, AT))

    assert json.loads(store._values["device-123"]) == {
        "version": 1,
        "handoff": {"etag": "a", "pace": 20, "at": "2026-10-18T12:00:00Z"},
    }


def test_encoding_round_trips():
    state = RenderState().handed_off("a", None, AT)

    assert decode(encode(state)) == state
    assert decode(encode(state).encode()) == state  # as bytes, as a Redis client that doesn't decode hands it back


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        '{"version": 2, "handoff": null}',  # from a newer renderer
        '{"version": 1, "handoff": {"etag": "a"}}',  # a field missing
        '{"version": 1, "handoff": {"etag": "a", "at": "2026-10-18T12:00:00"}}',  # a time with no zone
    ],
)
def test_a_state_that_cant_be_read_is_no_state(value):
    store = InMemoryRenderStateStore()
    store._values["device-123"] = value

    assert store.load("device-123") == RenderState()


def test_no_value_is_no_state():
    assert decode(None) == RenderState()
