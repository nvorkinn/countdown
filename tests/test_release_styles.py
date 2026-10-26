import countdown_renderer  # noqa: F401, I001  (import order: see test_display_snapshots.py)
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image, ImageDraw

from countdown_shared import i18n
from countdown_renderer.developer import release_styles
from countdown_shared.developer.models import Release, ReleaseJob

NOW = datetime(2026, 10, 17, 15, 4, tzinfo=UTC)


def _job(key, *needs, state="done"):
    return ReleaseJob(key=key, name=key, state=state, needs=list(needs))


def _release(state, *jobs):
    return Release(
        repo="auth-broker",
        title="v1.27.0",
        state=state,
        started=NOW - timedelta(minutes=6),
        finished=None if state == "running" else NOW,
        jobs=list(jobs),
    )


def _keys(columns):
    return [[job.key for job in column] for column in columns]


def test_each_job_goes_a_column_after_the_furthest_job_it_waits_for():
    jobs = [_job("tag"), _job("images", "tag"), _job("build", "tag"), _job("deploy", "tag", "images", "build")]

    assert _keys(release_styles._columns(jobs)) == [["tag"], ["images", "build"], ["deploy"]]


def test_a_need_that_isnt_a_job_or_a_loop_counts_for_nothing():
    jobs = [_job("a", "gone"), _job("b", "c"), _job("c", "b")]

    assert _keys(release_styles._columns(jobs)) == [["a", "c"], ["b"]]


def test_no_jobs_no_columns():
    assert release_styles._columns([]) == []


def test_a_line_another_need_already_covers_is_left_out():
    jobs = [_job("tag"), _job("images", "tag"), _job("deploy", "tag", "images"), _job("loop", "loop", "gone")]

    assert release_styles._lines(jobs) == {"tag": [], "images": ["tag"], "deploy": ["images"], "loop": ["loop"]}


@pytest.mark.parametrize(("length", "room", "rows"), [(4, 200, 1), (5, 200, 2), (5, 60, 1), (9, 200, 2)])
def test_a_chain_longer_than_four_wraps_onto_two_rows_when_they_fit(length, room, rows):
    columns = [[_job(str(c))] for c in range(length)]

    wrapped = release_styles._rows(columns, room)

    assert len(wrapped) == rows
    assert [column for row in wrapped for column in row] == columns
    # The first row takes the extra column of an odd chain.
    assert len(wrapped[0]) == (length + 1) // 2 if rows == 2 else length


@pytest.mark.parametrize(
    ("state", "jobs", "others", "text"),
    [
        ("running", [_job("tag", state="running")], 0, "6 min"),
        ("running", [_job("tag"), _job("deploy", "tag", state="approve")], 0, "6 min · approve deploy"),
        ("done", [_job("tag")], 2, "+2 more · released in 6 min"),
        ("failed", [_job("tag", state="failed")], 0, "failed after 6 min"),
    ],
)
def test_aside_says_how_long_and_what_waits_for_the_owner(state, jobs, others, text):
    assert release_styles.aside(_release(state, *jobs), others, NOW) == text


def test_aside_in_norwegian():
    i18n.set_locale("nb", "Europe/Oslo")
    try:
        text = release_styles.aside(_release("running", _job("deploy", state="approve")), 1, NOW)
    finally:
        i18n.set_locale("en", "Europe/London")

    assert text == "+1 til · 6 min · godkjenn deploy"


@pytest.mark.parametrize("style", list(release_styles.STYLES))
def test_each_style_draws_a_release_with_no_jobs_and_one_too_crowded_to_show_whole(style):
    """Neither divides by zero nor draws outside the panel."""
    builds = [_job(f"build {n}", "tag", state="running") for n in range(12)]
    crowded = [_job("tag"), *builds, _job("publish", *(build.key for build in builds), state="queued")]

    for release in (_release("running", *crowded), _release("running")):  # the broker may find no jobs yet
        image = release_styles.STYLES[style](release, 0, NOW, 300, 120)
        assert image.size == (300, 120)


# --- the boxes: black when done, a warning sign when the owner is needed ----------------------------------


BOX_WIDTH = 150


def _box(state, style="hero", name="deploy", total=None):
    """Draw one job's box on a white canvas, in `style`'s look, the way graph() does; return the picture."""
    look = release_styles.LOOKS[style]()
    job = ReleaseJob(key="job", name=name, state=state, done=1 if total else None, total=total)
    img = Image.new("RGB", (BOX_WIDTH + 4, release_styles.NODE_HEIGHT + 4), "white")
    release_styles._box(ImageDraw.Draw(img), job, (2, 2), BOX_WIDTH, look)
    return img


def _ink(img, left, right):
    """Return how many pixels from column `left` to `right`, between the box's top and bottom edges, aren't white."""
    rows = range(7, img.height - 7)
    return sum(1 for x in range(left, right) for y in rows if img.getpixel((x, y)) != (255, 255, 255))


@pytest.mark.parametrize("style", list(release_styles.STYLES))
def test_a_finished_job_is_a_black_box_with_white_ink(style):
    img = _box("done", style)
    middle = img.height // 2

    assert img.getpixel((BOX_WIDTH - 40, middle)) == (0, 0, 0)  # the box itself
    inside = [img.getpixel((x, y)) for x in range(8, BOX_WIDTH - 8) for y in range(5, img.height - 5)]
    assert (255, 255, 255) in inside  # the check mark and the name


@pytest.mark.parametrize("style", list(release_styles.STYLES))
@pytest.mark.parametrize("state", ["running", "queued", "skipped", "approve", "failed"])
def test_a_job_that_is_not_finished_is_a_white_box(style, state):
    img = _box(state, style)

    assert img.getpixel((BOX_WIDTH - 40, img.height // 2)) == (255, 255, 255)


@pytest.mark.parametrize("style", list(release_styles.STYLES))
@pytest.mark.parametrize("state", ["approve", "failed"])
def test_a_job_that_needs_the_owner_has_a_warning_sign_at_its_end(style, state):
    cell = (BOX_WIDTH - 6 - release_styles.LOOKS[style]().icon_size, BOX_WIDTH - 5)  # the sign's, inside the outline

    assert _ink(_box(state, style), *cell) > 20
    assert _ink(_box("queued", style), *cell) == 0


@pytest.mark.parametrize("state", ["running", "skipped"])
def test_the_other_unfinished_jobs_have_no_warning_sign(state):
    edge = (BOX_WIDTH - 24, BOX_WIDTH - 8)

    assert _ink(_box(state, "hero"), *edge) == _ink(_box("queued", "hero"), *edge)


def test_a_running_jobs_name_is_bolder_than_a_queued_ones():
    left = release_styles.LOOKS["hero"]().icon_size + 10
    assert _ink(_box("running", "hero", name="build"), left, BOX_WIDTH - 8) > _ink(
        _box("queued", "hero", name="build"), left, BOX_WIDTH - 8
    )


@pytest.mark.parametrize("style", list(release_styles.STYLES))
def test_a_long_name_stops_short_of_the_warning_sign(style):
    """The sign's own cell looks the same under a name that fits and under one that has to be cut short."""
    cell = (BOX_WIDTH - 6 - release_styles.LOOKS[style]().icon_size, BOX_WIDTH - 5)
    long = _box("approve", style, name="a-job-with-a-name-far-too-long-to-fit-in-its-box")

    assert _ink(long, *cell) == _ink(_box("approve", style, name="x"), *cell)


def test_the_icons_are_bare_glyphs_not_ones_inside_a_circle():
    circled = {0xE86C, 0xE5C9, 0xF08F, 0xE836}  # check_circle, cancel, do_not_disturb_on, radio_button_unchecked

    assert not circled & {ord(icon) for icon in release_styles.ICONS.values()}
    assert set(release_styles.ICONS) == {"done", "failed", "running", "approve", "queued", "skipped"}
