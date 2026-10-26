import pytest
from test_utils import assert_text_inside_and_apart

from countdown_renderer.notices.notice import Notice, Severity
from countdown_renderer.notices.notice_board_panel import ROW_HEIGHT, HdNoticeBoardPanel, NoticeBoardPanel
from countdown_renderer.notices.notice_styles import STYLES
from countdown_renderer.utils.scaled_draw import drawing_at

NOTICES = [
    Notice("Met Office", Severity.SEVERE, "Red warning: wind until Fri 21:00"),
    Notice("Circle", Severity.INFO, "Issues Reported"),
    Notice("TfL", Severity.WARNING, "Elephant & Castle: the lifts are out of service"),
]
TEXTS = {notice.text for notice in NOTICES}


def _assert_a_table(drawn_text) -> None:
    """Assert the texts are a column, the severe and warning ones each starting with an icon that sits in it."""
    lefts = {text: box[0] for text, box, _size in drawn_text}
    severe, info, warning = (lefts[notice.text] for notice in NOTICES)
    assert severe == warning > info
    icons = [box for text, box, _size in drawn_text if text == "!"]
    assert len(icons) == 2
    assert all(info <= box[0] and box[2] < severe for box in icons)
    sources = {lefts[notice.source] for notice in NOTICES}
    assert len(sources) == 1
    assert next(iter(sources)) < 3  # at the edge


def test_the_hd_board_is_a_table_whose_text_column_starts_where_the_widest_source_ends(drawn_text):
    board = HdNoticeBoardPanel(NOTICES)
    with drawing_at(2):
        img = board.render(692, board.fitted_height(190))

    _assert_a_table(drawn_text)
    assert {size for text, _box, size in drawn_text if text in TEXTS} == {30}  # 15, twice as big
    assert_text_inside_and_apart([call for call in drawn_text if call[0] != "!"], img.size)


def test_the_portrait_board_is_a_table_too(drawn_text):
    board = NoticeBoardPanel(NOTICES)
    img = board.render(470, board.fitted_height(190))

    _assert_a_table(drawn_text)
    assert_text_inside_and_apart([call for call in drawn_text if call[0] != "!"], img.size)


@pytest.mark.parametrize(("board", "row_height"), [(NoticeBoardPanel, ROW_HEIGHT), (HdNoticeBoardPanel, 28)])
@pytest.mark.parametrize("style", list(STYLES))
def test_a_board_is_as_tall_as_its_chrome_and_a_row_per_notice(board, row_height, style):
    chrome = STYLES[style]

    assert board(NOTICES, style).fitted_height(1000) == chrome.top + len(NOTICES) * row_height + chrome.bottom
    assert board(NOTICES, style).fitted_height(50) == 50
    assert board([], style).fitted_height(1000) == 0


def test_the_hd_board_is_made_from_the_portrait_one():
    hd = HdNoticeBoardPanel.of(NoticeBoardPanel(NOTICES, "card"))

    assert (hd.notices, hd.style) == (NOTICES, "card")


def test_the_hd_boards_rows_that_dont_fit_are_counted_on_the_last_one(drawn_text):
    many = [Notice(f"Source {n}", Severity.INFO, f"Notice {n}") for n in range(6)]
    chrome = STYLES["hero"]
    HdNoticeBoardPanel(many).render(692, chrome.top + 3 * 28 + chrome.bottom)

    texts = [text for text, _box, _size in drawn_text]
    assert [text for text in texts if text.startswith("Notice ")] == ["Notice 0", "Notice 1"]
    assert "+4 more" in texts


def test_info_reminders_and_planned_works_have_no_bullet():
    plain = [
        Notice("Circle", Severity.INFO, "Issues Reported"),
        Notice("Claude", Severity.REMINDER, "Max renews today"),
        Notice("Central", Severity.PLANNED, "Closed Sunday"),
    ]
    board = NoticeBoardPanel(plain)
    img = board.render(470, board.fitted_height(190)).convert("L")
    top = STYLES["hero"].top

    # The bullets drew 14px in; now nothing is drawn before each source, which starts at the edge.
    for row in range(len(plain)):
        y = top + row * ROW_HEIGHT
        assert img.crop((0, y, 1, y + ROW_HEIGHT)).getextrema() == (255, 255)
        assert img.crop((1, y + 4, 8, y + ROW_HEIGHT - 4)).getextrema()[0] < 128  # the source's first letter


def test_without_icons_the_sources_start_at_the_edge(drawn_text):
    plain = [Notice("Circle", Severity.INFO, "Issues Reported"), Notice("Central", Severity.PLANNED, "Closed Sunday")]
    NoticeBoardPanel(plain).render(470, NoticeBoardPanel(plain).fitted_height(190))

    lefts = {text: box[0] for text, box, _size in drawn_text}
    assert lefts["Circle"] < 2
    assert lefts["Circle"] == lefts["Central"]
    assert lefts["Issues Reported"] == lefts["Closed Sunday"]
