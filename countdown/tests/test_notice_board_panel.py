import pytest
from test_utils import assert_text_inside_and_apart

from countdown_core.notices.notice import Notice, Severity
from countdown_core.notices.notice_board_panel import ROW_HEIGHT, HdNoticeBoardPanel, NoticeBoardPanel
from countdown_core.notices.notice_styles import STYLES
from countdown_core.utils.scaled_draw import drawing_at

NOTICES = [
    Notice("Met Office", Severity.SEVERE, "Red warning: wind until Fri 21:00"),
    Notice("Circle", Severity.INFO, "Issues Reported"),
    Notice("TfL", Severity.WARNING, "Elephant & Castle: the lifts are out of service"),
]
TEXTS = {notice.text for notice in NOTICES}


def _text_lefts(drawn_text) -> set[float]:
    return {box[0] for text, box, _size in drawn_text if text in TEXTS}


def test_the_hd_board_is_a_table_whose_text_column_starts_where_the_widest_source_ends(drawn_text):
    board = HdNoticeBoardPanel(NOTICES)
    with drawing_at(2):
        img = board.render(692, board.fitted_height(190))

    assert len(_text_lefts(drawn_text)) == 1
    assert {size for text, _box, size in drawn_text if text in TEXTS} == {30}  # 15, twice as big
    assert_text_inside_and_apart(drawn_text, img.size)


def test_the_portrait_board_starts_each_rows_text_after_its_own_source(drawn_text):
    board = NoticeBoardPanel(NOTICES)
    board.render(470, board.fitted_height(190))

    assert len(_text_lefts(drawn_text)) == len(NOTICES)


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
