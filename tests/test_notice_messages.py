"""The notice board's words from countdown-api's messages (countdown_renderer.notices.messages), in each language.

The strings are countdown-api's (countdown_api.messages), formatted by ICU's MessageFormat (icu4py). What `say` must
return for each id the renderer uses is what gettext returned for the text it replaced, in English and in Norwegian,
so nothing drawn changed when the words moved (but for the English New Year's Day's apostrophe, now the typographic
one, as the catalogue has it).
"""

import ast
from pathlib import Path

import pytest

from countdown_api import messages as catalogues
from countdown_renderer.notices.messages import say
from countdown_shared import i18n

RENDERER = Path(__file__).resolve().parent.parent / "renderer" / "src" / "countdown_renderer"

# Each message the renderer uses, with arguments like the ones it's given, and what the screen must say.
SAID = [
    ("notice.line.tag", {"mode": "bus", "count": 1, "line": "133"}, "Bus 133", "133-bussen"),
    ("notice.line.tag", {"mode": "bus", "count": 2, "line": "415"}, "Buses", "Busser"),
    ("notice.weather.warning", {"colour": "red", "hazard": "wind"}, "Red warning: wind", "Rødt farevarsel: wind"),
    (
        "notice.weather.warning",
        {"colour": "amber", "hazard": "rain"},
        "Amber warning: rain",
        "Oransje farevarsel: rain",
    ),
    ("notice.weather.warning", {"colour": "yellow", "hazard": "ice"}, "Yellow warning: ice", "Gult farevarsel: ice"),
    (
        "notice.weather.warning",
        {"colour": "orange", "hazard": "kraftig vind"},
        "Orange warning: kraftig vind",
        "Oransje farevarsel: kraftig vind",
    ),
    (
        "notice.station.text",
        {"station": "Elephant & Castle", "detail": "No step-free access"},
        *["Elephant & Castle: No step-free access"] * 2,
    ),
    ("notice.road.tag", {}, "Roads", "Veier"),
    (
        "notice.road.text",
        {"category": "Collisions", "comments": "[A2] Old Kent Road"},
        *["Collisions: [A2] Old Kent Road"] * 2,
    ),
    ("notice.flood.tag", {}, "Floods", "Flom"),
    ("notice.flood.title", {"level": "severe"}, "Severe flood warning", "Alvorlig flomfare"),
    ("notice.flood.title", {"level": "warning"}, "Flood warning", "Flomfare"),
    ("notice.flood.title", {"level": "alert"}, "Flood alert", "Flomvarsel"),
    (
        "notice.flood.text",
        {"title": "Flood alert", "area": "Lower River Effra"},
        *["Flood alert: Lower River Effra"] * 2,
    ),
    ("notice.bins.tag", {}, "Bins", "Avfall"),
    ("notice.bins.text", {"when": "Tomorrow", "fractions": "Paper"}, "Tomorrow: Paper", "Tomorrow: Paper"),
    ("notice.bins.fraction", {"fraction": "residualWaste"}, "Residual waste", "Restavfall"),
    ("notice.bins.fraction", {"fraction": "glassContainer"}, "Glass and metal", "Glass og metall"),
    ("notice.bins.fraction", {"fraction": "compost"}, "Other waste", "Annet avfall"),
    ("notice.clock.tag", {}, "Clocks", "Klokker"),
    ("notice.clock.tonight", {}, "tonight", "i kveld"),
    ("notice.clock.on", {"day": "Sun 25 Oct"}, "on Sun 25 Oct", "på Sun 25 Oct"),
    (
        "notice.clock.text",
        {"direction": "forward", "when": "tonight"},
        "Clocks go forward an hour tonight, at",
        "Klokken stilles frem en time tonight, kl.",
    ),
    (
        "notice.clock.text",
        {"direction": "back", "when": "tonight"},
        "Clocks go back an hour tonight, at",
        "Klokken stilles tilbake en time tonight, kl.",
    ),
    ("notice.holiday.public_tag", {}, "Public holiday", "Helligdag"),
    # The catalogue's apostrophe.
    ("notice.holiday.name", {"holiday": "new_year"}, "New Year’s Day", "Første nyttårsdag"),  # noqa: RUF001
    ("notice.holiday.name", {"holiday": "maundy_thursday"}, "Maundy Thursday", "Skjærtorsdag"),
    ("notice.holiday.name", {"holiday": "good_friday"}, "Good Friday", "Langfredag"),
    ("notice.holiday.name", {"holiday": "easter_sunday"}, "Easter Sunday", "1. påskedag"),
    ("notice.holiday.name", {"holiday": "easter_monday"}, "Easter Monday", "2. påskedag"),
    ("notice.holiday.name", {"holiday": "labour_day"}, "Labour Day", "Arbeidernes dag"),
    ("notice.holiday.name", {"holiday": "constitution_day"}, "Constitution Day", "Grunnlovsdag"),
    ("notice.holiday.name", {"holiday": "ascension_day"}, "Ascension Day", "Kristi himmelfartsdag"),
    ("notice.holiday.name", {"holiday": "whit_sunday"}, "Whit Sunday", "Første pinsedag"),
    ("notice.holiday.name", {"holiday": "whit_monday"}, "Whit Monday", "Andre pinsedag"),
    ("notice.holiday.name", {"holiday": "christmas_day"}, "Christmas Day", "1. juledag"),
    ("notice.holiday.name", {"holiday": "boxing_day"}, "Boxing Day", "2. juledag"),
    ("notice.until", {"when": "Fri"}, "until Fri", "til Fri"),
    ("notice.holiday.tag", {}, "Bank holiday", "Offentlig fridag"),
    ("notice.holiday.text", {"when": "Fri 25 Dec", "name": "Christmas Day"}, *["Fri 25 Dec: Christmas Day"] * 2),
    (
        "notice.holiday.text_with_notes",
        {"when": "Mon 28 Dec", "name": "Boxing Day", "notes": "substitute day"},
        *["Mon 28 Dec: Boxing Day (substitute day)"] * 2,
    ),
    ("notice.day.today", {}, "Today", "I dag"),
    ("notice.day.tomorrow", {}, "Tomorrow", "I morgen"),
]


@pytest.fixture(autouse=True)
def english_afterwards():
    """Leave the screen in English on London's time, as every other test expects to find it."""
    yield
    i18n.set_locale("en", "Europe/London")


def said_ids() -> set[str]:
    """Return every message id the renderer's code passes to `say`: the first argument of each call, a literal."""
    ids = set()
    for path in RENDERER.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "say":
                first = node.args[0]
                assert isinstance(first, ast.Constant), f"{path.name}: say() of an id that isn't a literal"
                assert isinstance(first.value, str)
                ids.add(first.value)
    return ids


@pytest.mark.parametrize(("message_id", "arguments", "english", "norwegian"), SAID)
def test_a_message_is_said_in_the_screens_language(message_id, arguments, english, norwegian):
    i18n.set_locale("en", "Europe/London")
    assert say(message_id, **arguments) == english

    i18n.set_locale("nb", "Europe/Oslo")
    assert say(message_id, **arguments) == norwegian


def test_the_next_screen_in_another_language_gets_its_own_words():
    # One process draws every screen, each in its own language: a formatter kept for one mustn't answer for another.
    said = []
    for language in ("en", "nb", "en", "nb"):
        i18n.set_locale(language, "Europe/London")
        said.append(say("notice.until", when=say("notice.day.tomorrow")))

    assert said == ["until Tomorrow", "til I morgen"] * 2


def test_an_id_with_no_message_raises_rather_than_being_drawn():
    with pytest.raises(KeyError, match="no_such_message"):
        say("notice.no_such_message")


@pytest.mark.parametrize("language", catalogues.LANGUAGES)
@pytest.mark.parametrize(
    "value", ["{", "}", "{line}", "'", "It's {quoted} '{' here", "#", "Route #5 {count, plural, one {x} other {y}}"]
)
def test_an_argument_is_put_in_as_it_is_whatever_icu_would_make_of_it(language, value):
    i18n.set_locale(language, "Europe/London")

    assert say("notice.line.tag", mode="tube", count=1, line=value) == value
    assert say("notice.holiday.text", when="Fri", name=value) == f"Fri: {value}"
    assert say("notice.weather.warning", colour="red", hazard=value).endswith(f": {value}")


def test_every_message_the_renderer_says_is_tested_here_and_is_in_every_language():
    ids = said_ids()

    assert ids == {message_id for message_id, *_ in SAID}
    for language in catalogues.LANGUAGES:
        assert ids <= set(catalogues.catalog(language)), language
