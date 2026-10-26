"""Which page a rotating panel shows: worked out from the clock, not counted.

Every panel that turns pages (the departures' stops, the weather's tabs, the energy panel's windows, the Spotify area's
player and top lists) shows page `epoch minute mod pages`, each with its own number of pages. So every screen turns its
pages together, on the minute; two draws in the same minute show the same page; and nothing about the rotation is kept
between them: a client rebuilt by a config change carries on where the clock is, rather than from its first page.

The minute is counted from the epoch rather than within the hour, so a count that doesn't divide 60 (seven pages of
stops, say) doesn't jump back to its first page on the hour.
"""

from typing import TYPE_CHECKING

from countdown_renderer import moment

if TYPE_CHECKING:
    from datetime import datetime

# How long each page is shown, in seconds.
PAGE_S = 60


def now() -> datetime:
    """Return the time the pages turn by: the moment being drawn (core.moment). The tests replace it to freeze it."""
    return moment.now()


def page(pages: int, at: datetime | None = None) -> int:
    """Return which of `pages` pages to show at `at` (by default, now): the minute since the epoch, modulo `pages`.

    Raises ValueError for fewer than one page: a panel with nothing to rotate through shows something else.
    """
    if pages < 1:
        msg = f"There must be at least one page to show, not {pages}"
        raise ValueError(msg)
    at = at if at is not None else now()
    return int(at.timestamp() // PAGE_S) % pages
