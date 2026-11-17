"""The notice board's words that every kind of screen says: countdown-api's messages, in the screen's language.

What a touchscreen's app says too (a line's tag, a weather warning, a bank holiday, "Today") is worded once, in
countdown-api (countdown_api.messages: ICU MessageFormat strings by id, in each language), so both say the same thing.
`say` formats one with ICU's own MessageFormat (icu4py), for the language the screen is drawn in:

    say("notice.line.tag", mode="bus", count=1, line="133")   # "Bus 133", or "133-bussen"
    say("notice.until", when="Fri")                           # "until Fri", or "til Fri"

A date or a time is formatted here first (i18n.format_date) and passed in as text. What only an e-paper screen says
stays in gettext: `_()` and locale/<language>.po (countdown_shared.i18n).
"""

from functools import cache

from icu4py.messageformat import MessageFormat

from countdown_api import messages
from countdown_shared.i18n import language


@cache
def _formatter(language: str, message_id: str) -> MessageFormat:
    """Return the formatter of the message `message_id` in `language`, parsed once; KeyError if there's none."""
    return MessageFormat(messages.catalog(language)[message_id], language)


def say(message_id: str, /, **arguments: str | int) -> str:
    """Return the message `message_id` in the screen's language, with `arguments` put in as they are.

    Raises KeyError for an id countdown-api has no message for: a mistake in the code, never something to draw.
    """
    return _formatter(language(), message_id).format({**arguments})
