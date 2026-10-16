"""The language the screen is drawn in and its time zone: the broker's `language` and `timezone`, one per renderer
(one renderer draws one screen).

Every text the screen shows is English in the code and translated as it's drawn, so a change of language shows on
the next frame. Mark a text with `_()` where it's drawn, or with `N_()` where it's defined and drawn elsewhere (then
`_()` it there). The translations are gettext catalogues, locale/<language>.po, read as they are, so editing one
needs no build step; anything not translated yet is drawn in English. `sh dev/update_translations.sh` gathers the
texts and brings the catalogues in line with them (see the README).
"""

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from babel import Locale
from babel.dates import format_date as babel_format_date
from babel.messages.pofile import read_po

if TYPE_CHECKING:
    from datetime import date

Language = Literal["en", "nb"]
LOCALE_DIR = Path(__file__).resolve().parent / "locale"

_language: Language = "en"
_timezone = ZoneInfo("Europe/London")


def set_locale(language: Language, timezone: str) -> None:
    """Called with each config, before the clients are built."""
    global _language, _timezone  # noqa: PLW0603 -- one renderer, one screen, one language
    _language = language
    _timezone = ZoneInfo(timezone)


def language() -> Language:
    return _language


def local_zone() -> ZoneInfo:
    """The screen's time zone: what its "today" and its clock go by."""
    return _timezone


@cache
def _catalogue(language: Language) -> dict[str | tuple[str, str], str | tuple[str, ...]]:
    """The language's translations, by English text (a (singular, plural) pair for a plural). Empty for English
    or a language with no catalogue.
    """
    path = LOCALE_DIR / f"{language}.po"
    if language == "en" or not path.exists():
        return {}
    with path.open("rb") as f:
        catalogue = read_po(f, locale=language)
    return {message.id: message.string for message in catalogue if message.id and message.string and not message.fuzzy}


def _(text: str) -> str:
    """`text` in the screen's language, or as it is if it isn't translated."""
    translated = _catalogue(_language).get(text)
    return translated if isinstance(translated, str) and translated else text


def ngettext(singular: str, plural: str, n: int) -> str:
    """The singular or plural form for `n`, in the screen's language, e.g. ngettext("{n} min", "{n} mins", 3).
    Doesn't format: call .format(n=n) on what it returns.
    """
    forms = _catalogue(_language).get((singular, plural))
    if isinstance(forms, tuple) and all(forms):
        return forms[_plural_index(_language, n)]
    return singular if n == 1 else plural


def _plural_index(language: Language, n: int) -> int:
    # English and Norwegian both have one form for 1 and another for everything else.
    return 0 if n == 1 else 1


def N_(text: str) -> str:  # noqa: N802 -- gettext's name for it
    """Marks `text` for translation without translating it: for texts defined in one place and drawn in another,
    which translates them with _().
    """
    return text


def format_date(day: date, pattern: str) -> str:
    """`day` in the screen's language, with a CLDR pattern: "EEE d MMM" is "Fri 16 Oct" or "fre. 16. okt."."""
    return babel_format_date(day, pattern, locale=Locale.parse(_language))
