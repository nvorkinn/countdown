import countdown_core  # noqa: F401, I001  (import order: see test_display_snapshots.py)
from datetime import UTC, datetime, timedelta

import pytest

from countdown_core import i18n
from countdown_core.developer import release_styles
from countdown_core.developer.models import Release, ReleaseJob

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
