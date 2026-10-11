"""The raw panel buffer a display target sends. ServerTarget is covered in test_server_target.py."""

from PIL import Image

from countdown_core.core.targets import panel_bytes


def test_white_pixels_are_sent_as_zero_bits_and_black_as_one():
    white = Image.new("1", (16, 1), 1)
    black = Image.new("1", (16, 1), 0)

    assert panel_bytes(white) == bytearray([0x00, 0x00])
    assert panel_bytes(black) == bytearray([0xFF, 0xFF])


def test_a_region_sends_only_its_own_bytes():
    img = Image.new("1", (800, 480), 1)
    img.paste(0, (8, 10, 16, 11))  # one black byte, inside the region

    buf = panel_bytes(img, (8, 10, 24, 12))

    assert buf == bytearray([0xFF, 0x00, 0x00, 0x00])  # 16px wide = 2 bytes a row, 2 rows
