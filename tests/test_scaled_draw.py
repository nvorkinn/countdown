import countdown_renderer  # noqa: F401, I001 -- see test_display_snapshots.py for why this comes first
from PIL import Image, ImageDraw

from countdown_renderer.utils.scaled_draw import ScaledDraw, drawing_at, drawing_scale, scaled_canvas
from countdown_renderer.utils.utils import GOOGLE_SEMI


def _shapes(d: ImageDraw.ImageDraw | ScaledDraw) -> None:
    d.rectangle((2, 3, 9, 6), fill="black")
    d.rectangle((12, 2, 18, 8), outline="black", width=1)
    d.line((0, 12, 19, 12), fill="black", width=1)
    d.point((5, 15), fill="black")


def test_at_scale_one_it_draws_exactly_what_imagedraw_does():
    """So every panel on the 7.5" screens is drawn as it always was."""
    plain = Image.new("RGBA", (40, 30), (255, 255, 255, 0))
    draw = ImageDraw.Draw(plain)
    draw.fontmode = "1"
    _shapes(draw)
    draw.rounded_rectangle((20, 2, 38, 12), 4, outline="black", width=2)
    draw.text((20, 20), "Ag", "black", font=GOOGLE_SEMI, anchor="lm")

    scaled, d = scaled_canvas(40, 30)
    _shapes(d)
    d.rounded_rectangle((20, 2, 38, 12), 4, outline="black", width=2)
    d.text((20, 20), "Ag", "black", font=GOOGLE_SEMI, anchor="lm")

    assert scaled.tobytes() == plain.tobytes()


def _boxes(d: ScaledDraw) -> None:
    d.rectangle((2, 3, 9, 6), fill="black")
    d.rectangle((12, 2, 18, 8), outline="black", width=1)
    d.point((5, 15), fill="black")


def test_boxes_and_points_cover_the_pixels_theirs_grow_to():
    """At scale 3 they're exactly the small drawing with each pixel repeated."""
    small, d = scaled_canvas(20, 20)
    _boxes(d)
    with drawing_at(3):
        big, d = scaled_canvas(20, 20)
        _boxes(d)

    assert big.size == (60, 60)
    assert big.tobytes() == small.resize((60, 60), Image.Resampling.NEAREST).tobytes()


def test_a_line_runs_through_the_middle_of_the_pixels_it_grows_to():
    with drawing_at(3):
        img, d = scaled_canvas(20, 20)
        d.line((2, 12, 17, 12), fill="black", width=1)

    # Three rows thick, where the small line's row grew to; its ends stop at the end pixels' middles.
    assert img.getbbox() == (7, 36, 53, 39)


def test_text_is_set_at_the_bigger_size():
    with drawing_at(2):
        img, d = scaled_canvas(40, 20)
        d.text((0, 0), "Ag", "black", font=GOOGLE_SEMI)
    box = img.getbbox()

    assert box is not None
    assert box[3] - box[1] > GOOGLE_SEMI.getbbox("Ag")[3] * 1.5


def test_the_drawing_scale_is_one_outside_drawing_at():
    with drawing_at(2):
        assert drawing_scale() == 2
    assert drawing_scale() == 1


def test_at_a_scale_that_isnt_whole_a_box_covers_the_screen_pixels_nearest_its_own():
    with drawing_at(2.5):
        img, d = scaled_canvas(20, 20)
        d.rectangle((1, 2, 4, 2), fill="black")

    assert img.size == (50, 50)
    # Columns 1 to 4 span 2.5 to 12.5, rounded to 2 to 12 (half to even); row 2 spans 5 to 7.5, rounded to 5 to 8.
    assert img.getbbox() == (2, 5, 12, 8)
