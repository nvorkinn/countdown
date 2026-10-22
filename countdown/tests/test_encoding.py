"""The bytes a frame is encoded to, by the screen's format."""

import io

from PIL import Image

from countdown_api.models import Screen
from countdown_core.rendering.encoding import ENCODERS, encode


def _screen(fmt: str) -> Screen:
    return Screen(orientation="horizontal", width=16, height=1, format=fmt)


def test_mono_1bpp_packed_is_the_panels_raw_buffer():
    assert encode(Image.new("1", (16, 1), 0), _screen("mono-1bpp-packed")) == b"\xff\xff"


def test_png_decodes_to_the_same_picture():
    img = Image.new("1", (16, 2), 0)
    img.putpixel((3, 1), 1)

    decoded = Image.open(io.BytesIO(encode(img, _screen("png"))))

    assert decoded.format == "PNG"
    assert list(decoded.convert("1").tobytes()) == list(img.tobytes())


def test_every_format_the_api_allows_has_an_encoder():
    assert set(ENCODERS) == set(Screen.model_fields["format"].annotation.__args__)


def test_mono_1bpp_packed_sends_white_as_zero_bits_and_black_as_one():
    assert encode(Image.new("1", (16, 1), 1), _screen("mono-1bpp-packed")) == b"\x00\x00"
