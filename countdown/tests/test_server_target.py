import pytest
import requests
import responses
from PIL import Image

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT
from countdown_core.core.targets import DisplayTarget, panel_bytes
from countdown_credentials.registration import RendererRegistration
from countdown_server import main as server_main
from countdown_server.server_target import ServerTarget

BROKER_URL = "https://broker.example.com"
FRAME_URL = f"{BROKER_URL}/api/frame"


@pytest.fixture
def broker():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def target() -> ServerTarget:
    return ServerTarget(RendererRegistration(BROKER_URL, "device-secret", "device-123"))


def _frame() -> Image.Image:
    img = Image.new("1", (800, 480), 1)
    img.paste(0, (0, 0, 400, 480))  # left half black
    return img


def test_is_a_display_target(target):
    assert isinstance(target, DisplayTarget)


def test_talks_to_the_registrations_broker(target):
    assert target.base_url == BROKER_URL


def test_paint_puts_the_panel_bytes_to_the_frame(target, broker):
    broker.put(FRAME_URL)
    img = _frame()

    assert target.paint(img) is True

    [call] = broker.calls
    assert call.request.body == bytes(panel_bytes(img))
    assert call.request.headers["Authorization"] == "Bearer device-secret"
    assert call.request.req_kwargs["timeout"] == DEFAULT_TIMEOUT


def test_paint_is_accepted_while_waiting_for_a_screen(target, broker):
    broker.put(FRAME_URL, status=202)

    assert target.paint(_frame()) is True


def test_paint_with_a_region_still_sends_the_whole_frame(target, broker):
    broker.put(FRAME_URL)
    img = _frame()
    region = (392, 0, 408, 8)

    target.paint(img, region)

    [call] = broker.calls
    assert call.request.body == bytes(panel_bytes(img))
    assert len(call.request.body) == 800 * 480 // 8  # the only length the broker accepts


def test_paint_raises_when_the_broker_rejects_the_frame(target, broker):
    broker.put(FRAME_URL, status=401)

    with pytest.raises(requests.HTTPError, match="401"):
        target.paint(_frame())


def test_run_starts_the_app_with_the_server_target(mocker):
    app_main = mocker.patch.object(server_main, "main")

    server_main.run()

    app_main.assert_called_once_with(ServerTarget)
