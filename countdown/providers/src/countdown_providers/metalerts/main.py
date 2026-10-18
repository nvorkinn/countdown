"""The MetAlerts provider: MET Norway's current weather warnings for all of Norway, written to `v1:notices:metalerts`.

MetAlerts answers for the whole country at once (current.json without a place), so one answer serves every Norwegian
notice board, whatever its postcode: the renderer picks out the warnings whose area covers its own place
(countdown_core.notices.norway.StoredMetAlertsSource). So it reads the broker's postcodes (`v1:sub:postcode`) only to
know whether any screen is in Norway (a Norwegian postcode is four digits; the UK's are other providers'), asks MET
nothing while none is, and needs no postcode's place.

It keeps to MET's terms (https://api.met.no/doc/TermsOfService): every request says who's asking (MET_HEADERS), an
answer is kept until its Expires before MET is asked again, and then with If-Modified-Since, so an unchanged answer
costs a 304. It asks in both languages the screens are drawn in, Norwegian (lang=no) and English (lang=en): two small
requests every five minutes, however many screens.

The HTTP calls are synchronous (requests), so they run in a worker thread (asyncio.to_thread), like the TfL provider's.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.core.log_format import configure_logging
from countdown_core.notices.norway import MET_HEADERS, METALERTS_URL
from countdown_core.store import keys
from countdown_core.store.metalerts import StoredMetAlerts
from countdown_providers.config import Settings
from countdown_providers.runner import serve

if TYPE_CHECKING:
    from collections.abc import Callable

    import requests

logger = logging.getLogger(__name__)

# As the broker writes them (its pages.NORWEGIAN_POSTCODE_RE): a UK postcode always has letters.
NORWEGIAN_POSTCODE = re.compile(r"[0-9]{4}")
# MetAlerts' `lang`s, one for each language a screen can be drawn in: Norwegian (nb) and English.
LANGUAGES = ("no", "en")
# How long an answer that came without an Expires is kept: long enough that a second batch of postcodes in the same
# cycle doesn't ask again, short enough that the next cycle does.
WITHOUT_EXPIRES = timedelta(minutes=1)


def is_norwegian(postcode: str) -> bool:
    """Whether `postcode`, in the broker's canonical form, is Norwegian: four digits ("0150")."""
    return NORWEGIAN_POSTCODE.fullmatch(postcode) is not None


@dataclass(frozen=True)
class Answer:
    """MET's last answer in one language, and what it said about caching it."""

    payload: dict[str, Any]
    # When MET sent it, or last said it hadn't changed (a 304).
    confirmed_at: datetime
    # Until when MET says it's current (its Expires): it isn't asked again before then.
    expires: datetime
    # MET's Last-Modified for it, sent back as If-Modified-Since.
    last_modified: str | None


def expires(response: requests.Response, now: datetime) -> datetime:
    """Return when `response` says it goes out of date (its Expires), or WITHOUT_EXPIRES from `now` if it doesn't."""
    header = response.headers.get("Expires")
    try:
        when = parsedate_to_datetime(header) if header else None
    except TypeError, ValueError:
        when = None
    if when is None:
        return now + WITHOUT_EXPIRES
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


class MetAlertsProvider:
    """MET Norway's weather warnings for all of Norway, from MetAlerts (keyless: it only wants a User-Agent)."""

    name = keys.POSTCODE
    # Six cycles: a few failed ones don't take the warnings off the boards, a provider that's down soon does.
    ttl_s = 30 * 60
    # Warnings are issued hours ahead, and most answers are a 304 within MET's Expires.
    interval_s = 5 * 60

    def __init__(self, session: requests.Session, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Ask MET through `session`; `now` is the clock fetched_at and Expires go by (a parameter for the tests)."""
        self.session = session
        self.now = now
        # The last answer in each language, to send If-Modified-Since with, and to reuse until its Expires.
        self.answers: dict[str, Answer] = {}

    def output_key(self, item_id: str) -> str:
        """Write to `v1:notices:metalerts`, whichever postcode: the one answer is for all of Norway."""
        del item_id
        return keys.metalerts()

    async def fetch(self, ids: list[str]) -> dict[str, StoredMetAlerts]:
        """Fetch Norway's warnings if any of `ids` is a Norwegian postcode; raises if MET can't be asked.

        Returned under one of those postcodes only: the runner writes a result per id, and every one would go to the
        same key. A failure raises, failing the batch, so the key keeps MET's last answer until it expires.
        """
        norwegian = [postcode for postcode in ids if is_norwegian(postcode)]
        if not norwegian:
            return {}
        stored = await asyncio.to_thread(self._fetch_blocking)
        return {norwegian[0]: stored}

    def _fetch_blocking(self) -> StoredMetAlerts:
        answers = {lang: self._answer(lang) for lang in LANGUAGES}
        return StoredMetAlerts(
            fetched_at=min(answer.confirmed_at for answer in answers.values()),
            no=answers["no"].payload,
            en=answers["en"].payload,
        )

    def _answer(self, lang: str) -> Answer:
        """Return MET's current answer in `lang`: the last one until its Expires, then asked again if it's changed."""
        now = self.now()
        last = self.answers.get(lang)
        if last is not None and now < last.expires:
            return last
        headers = dict(MET_HEADERS)
        if last is not None and last.last_modified:
            headers["If-Modified-Since"] = last.last_modified
        response = self.session.get(METALERTS_URL, params={"lang": lang}, headers=headers, timeout=DEFAULT_TIMEOUT)
        if response.status_code == HTTPStatus.NOT_MODIFIED and last is not None:
            payload, last_modified, unchanged = last.payload, last.last_modified, True
        else:
            response.raise_for_status()
            payload, last_modified, unchanged = response.json(), response.headers.get("Last-Modified"), False
        answer = Answer(payload, now, expires(response, now), last_modified)
        self.answers[lang] = answer
        logger.debug(
            "MetAlerts (%s): %s, %d warnings, current until %s",
            lang,
            "unchanged" if unchanged else "new answer",
            len(payload.get("features") or []),
            answer.expires.isoformat(),
        )
        return answer


def main() -> None:
    """Run the `countdown-provider-metalerts` console script."""
    configure_logging()
    # The renderer's own retrying session: retries connection errors and 502/503/504 with backoff.
    session = AbstractClient._build_retrying_session()  # noqa: SLF001 -- shared with the renderer's clients on purpose
    asyncio.run(serve(MetAlertsProvider(session), Settings.from_env()))


if __name__ == "__main__":
    main()
