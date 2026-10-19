"""The raw panel buffer a frame is: what the frame record holds and the screen takes."""

from PIL import Image

from countdown_core.rendering.screen import panel_bytes


def test_white_pixels_are_sent_as_zero_bits_and_black_as_one():
    white = Image.new("1", (16, 1), 1)
    black = Image.new("1", (16, 1), 0)

    assert panel_bytes(white) == bytearray([0x00, 0x00])
    assert panel_bytes(black) == bytearray([0xFF, 0xFF])
