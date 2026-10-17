"""The looks the developer panel can have (the config's style).

Each matches the Spotify style of the same name: GitHub on the left half, the AI plans on the right. Each draws into
`width` x `height`, on a transparent background.
"""

from typing import TYPE_CHECKING

from countdown_core.i18n import _, format_date, ngettext
from countdown_core.spotify.spotify_styles import CARD_BAR, _card, _hero_header
from countdown_core.tfl.arrival_styles import BOLD, MEDIUM, REGULAR, SANS, SANS_SEMI, _canvas
from countdown_core.utils.utils import fitted_font, google_symbols, truncate_to_fit

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL import Image, ImageDraw, ImageFont

    from countdown_core.developer.models import AiPlan, DeveloperSummary, GitHubSummary

# Material Symbols glyphs
CODE = chr(0xE86F)
SPARKLE = chr(0xE65F)
CANCEL = chr(0xE5C9)
CHECK_CIRCLE = chr(0xE86C)
RENEW = chr(0xE863)

# How far the plans' bars and plan names start from their names.
NAME_WIDTH = 62


def _repos(github: GitHubSummary | None) -> str:
    return "" if github is None else ngettext("{n} repo", "{n} repos", github.repos).format(n=github.repos)


def _builds(github: GitHubSummary) -> str:
    """All builds passing, the one repo failing, or how many are and which."""
    failing = github.failing
    if not failing:
        return _("All builds passing")
    if len(failing) == 1:
        return _("{repo} failing").format(repo=failing[0])
    return _("{n} failing: {repos}").format(n=len(failing), repos=", ".join(failing))


def _no_github(d: ImageDraw.ImageDraw, x: float, y: float, width: float, font: ImageFont.FreeTypeFont) -> None:
    d.text((x, y), truncate_to_fit(_("Add a GitHub token on the config page"), font, width), "black", font, anchor="lm")


def _halves(width: int, gap: int) -> tuple[int, int, int, int]:
    """Return the left half's left and right, then the right half's."""
    column = (width - gap) // 2
    return 0, column, column + gap, width


def _plans(  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, and in which fonts
    d: ImageDraw.ImageDraw,
    plans: list[AiPlan],
    left: float,
    right: float,
    top: float,
    step: float,
    name_font: ImageFont.FreeTypeFont,
    small: ImageFont.FreeTypeFont,
    bar: Callable[[ImageDraw.ImageDraw, float, float, float, float], None],
) -> None:
    """Draw a row per plan: its name, then a bar of how much is used (or the plan's name), then when it renews."""
    if not plans:
        d.text((left, top), _("No plans"), "black", small, anchor="lm")
        return
    dates = [format_date(plan.renews, "d MMM") for plan in plans]
    date_width = max(small.getlength(text) for text in dates)
    renew = google_symbols().font_variant(size=14)
    for i, (plan, renews) in enumerate(zip(plans, dates, strict=True)):
        y = top + i * step
        d.text((left, y), truncate_to_fit(plan.name, name_font, NAME_WIDTH - 6), "black", name_font, anchor="lm")
        d.text((right, y), renews, "black", small, anchor="rm")
        d.text((right - date_width - 4, y), RENEW, "black", renew, anchor="rm")
        end = right - date_width - 28
        share = plan.share_used
        if share is None:
            d.text(
                (left + NAME_WIDTH, y),
                truncate_to_fit(plan.plan, small, end - left - NAME_WIDTH),
                "black",
                small,
                anchor="lm",
            )
        else:
            d.text((end, y), f"{round(100 * share)}%", "black", small, anchor="rm")
            bar(d, left + NAME_WIDTH, y, end - 40, min(share, 1))


def _hairline_bar(d: ImageDraw.ImageDraw, left: float, y: float, right: float, share: float) -> None:
    d.line((left, y, right, y), fill="black")
    d.rectangle((left, y - 2, left + (right - left) * share, y + 2), fill="black")


def _outlined_bar(width: int) -> Callable[[ImageDraw.ImageDraw, float, float, float, float], None]:
    def bar(d: ImageDraw.ImageDraw, left: float, y: float, right: float, share: float) -> None:
        d.rounded_rectangle((left, y - 5, right, y + 5), 3, outline="black", width=width)
        if share:
            d.rounded_rectangle((left, y - 5, left + (right - left) * share, y + 5), 3, fill="black")

    return bar


def _counts(  # noqa: PLR0913, PLR0917 -- a drawing helper: where to draw, what, and in which fonts
    d: ImageDraw.ImageDraw,
    github: GitHubSummary,
    x: float,
    y: float,
    big: ImageFont.FreeTypeFont,
    small: ImageFont.FreeTypeFont,
    anchors: tuple[str, str],
) -> None:
    """Draw the open pull requests and the reviews waiting, each a big number with its label after it."""
    for n, label in ((github.open_prs, _("open PRs")), (github.review_requests, _("to review"))):
        d.text((x, y), str(n), "black", big, anchor=anchors[0])
        x += big.getlength(str(n)) + 6
        d.text((x, y - 2 if anchors[1] == "ls" else y + 1), label, "black", small, anchor=anchors[1])
        x += small.getlength(label) + 22


# --- airy: small-caps labels and a hairline, like the weather -------------------------------


def _airy_label(d: ImageDraw.ImageDraw, x: float, y: float, icon: str, text: str) -> None:
    """Draw a small icon and a small-caps label after it, centred on `y`."""
    d.text((x, y), icon, "black", google_symbols().font_variant(size=15), anchor="lm")
    d.text((x + 18, y), text, "black", fitted_font(SANS_SEMI, 11), anchor="lm")


def airy(summary: DeveloperSummary, width: int, height: int) -> Image.Image:
    """Draw GitHub's counts big and its builds under a hairline, beside a row per plan, each under a small label."""
    img, d = _canvas(width, height)
    left, right, plans_left, plans_right = _halves(width, 24)
    github = summary.github
    _airy_label(d, left, 9, CODE, "GITHUB")
    d.text((right, 9), _repos(github).upper(), "black", fitted_font(SANS_SEMI, 11), anchor="rm")
    d.line((left, 19, right, 19), fill="black")
    body = fitted_font(SANS, 15)
    if github is None:
        _no_github(d, left, 50, right - left, body)
    else:
        _counts(d, github, left, 60, fitted_font(SANS, 32), fitted_font(SANS, 14), ("ls", "ls"))
        d.line((left, 86, right, 86), fill="black")
        d.text(
            (left, 104),
            CANCEL if github.failing else CHECK_CIRCLE,
            "black",
            google_symbols().font_variant(size=16),
            anchor="lm",
        )
        d.text((left + 20, 104), truncate_to_fit(_builds(github), body, right - left - 20), "black", body, anchor="lm")

    _airy_label(d, plans_left, 9, SPARKLE, _("AI PLANS"))
    d.line((plans_left, 19, plans_right, 19), fill="black")
    _plans(
        d,
        summary.plans[:3],
        plans_left,
        plans_right,
        40,
        30,
        fitted_font(SANS_SEMI, 14),
        fitted_font(SANS, 13),
        _hairline_bar,
    )
    return img


# --- hero: an icon header over a heavy rule, and the pull requests big, like the next bus ---


def hero(summary: DeveloperSummary, width: int, height: int) -> Image.Image:
    """Draw the open pull requests big in a black badge, the builds in a FAIL or OK badge's row, beside the plans."""
    img, d = _canvas(width, height)
    left, right, plans_left, plans_right = _halves(width, 24)
    github = summary.github
    _hero_header(d, left, right, CODE, "GitHub", _repos(github))
    if github is None:
        _no_github(d, left, 50, right - left, fitted_font(REGULAR, 14))
    else:
        y = 34
        d.rounded_rectangle((left, y, left + 40, y + 40), 6, fill="black")
        d.text((left + 20, y + 20), str(github.open_prs), "white", fitted_font(BOLD, 24), anchor="mm")
        room = right - left - 48
        title = fitted_font(BOLD, 18)
        d.text((left + 48, y + 12), truncate_to_fit(_("open PRs"), title, room), "black", title, anchor="lm")
        waiting = ngettext("{n} waiting for your review", "{n} waiting for your review", github.review_requests)
        subtitle = fitted_font(REGULAR, 12)
        d.text(
            (left + 48, y + 31),
            truncate_to_fit(waiting.format(n=github.review_requests), subtitle, room),
            "black",
            subtitle,
            anchor="lm",
        )
        y = 92
        badge = _("FAIL") if github.failing else _("OK")
        badge_font = fitted_font(BOLD, 12)
        badge_width = badge_font.getlength(badge) + 16
        box = (left, y, left + badge_width, y + 20)
        if github.failing:
            d.rounded_rectangle(box, 4, fill="black")
        else:
            d.rounded_rectangle(box, 4, outline="black", width=2)
        d.text((left + badge_width / 2, y + 10), badge, "white" if github.failing else "black", badge_font, anchor="mm")
        builds = _builds(github) if len(github.failing) != 1 else github.failing[0]
        font = fitted_font(MEDIUM, 14)
        room = right - left - badge_width - 8
        d.text((left + badge_width + 8, y + 10), truncate_to_fit(builds, font, room), "black", font, anchor="lm")

    _hero_header(d, plans_left, plans_right, SPARKLE, _("AI plans"), "")
    _plans(
        d,
        summary.plans[:3],
        plans_left,
        plans_right,
        46,
        27,
        fitted_font(MEDIUM, 14),
        fitted_font(REGULAR, 13),
        _outlined_bar(2),
    )
    return img


# --- card: a rounded card with a black title bar, like the card arrivals ---------------------


def card(summary: DeveloperSummary, width: int, height: int) -> Image.Image:
    """Draw GitHub and the plans in a card each, the builds under a dotted line."""
    img, d = _canvas(width, height)
    left, right, plans_left, plans_right = _halves(width, 10)
    github = summary.github
    _card(d, left, right, height, CODE, "GitHub", _repos(github))
    small = fitted_font(SANS, 14)
    if github is None:
        _no_github(d, left + 12, CARD_BAR + 22, right - left - 24, small)
    else:
        _counts(d, github, left + 12, CARD_BAR + 22, fitted_font(BOLD, 24), small, ("lm", "lm"))
        for dot in range(left + 12, right - 10, 4):
            d.point((dot, 84), fill="black")
        d.text(
            (left + 12, 100),
            CANCEL if github.failing else CHECK_CIRCLE,
            "black",
            google_symbols().font_variant(size=18),
            anchor="lm",
        )
        d.text(
            (left + 34, 100), truncate_to_fit(_builds(github), small, right - left - 46), "black", small, anchor="lm"
        )

    _card(d, plans_left, plans_right, height, SPARKLE, _("AI plans"), "")
    _plans(
        d,
        summary.plans[:3],
        plans_left + 12,
        plans_right - 11,
        CARD_BAR + 17,
        25,
        fitted_font(SANS_SEMI, 14),
        fitted_font(SANS, 13),
        _outlined_bar(1),
    )
    return img


STYLES: dict[str, Callable[[DeveloperSummary, int, int], Image.Image]] = {"airy": airy, "hero": hero, "card": card}
