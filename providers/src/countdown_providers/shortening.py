"""Asks the model for the short form of the texts a provider just fetched, once each, and keeps the answers.

The provider of TfL's or Entur's notices calls ensure() with the prose it fetched, each cycle. A text whose answer is
in the store (`v1:notice-short:<digest>`, countdown_shared.notices.shortening) costs one MGET with the others and has
its TTL renewed, so an answer lives as long as its text is around and a week after. One that isn't is asked about, a
few a cycle (NEW_PER_CYCLE): a morning of disruption is spread over a few cycles, not one long one, and the notice
board cuts the text itself meanwhile. A text the model refuses, or answers unfit to show, is remembered as failed for
an hour, so it isn't paid for again every cycle; when the model can't be reached, or fails, the cycle asks no more and
nothing is remembered.

ensure() never raises: a notice without its short form is still a notice. Nothing logged here has a text in it, only
how many, the model's status and an error's type; the model's client logs each call and what the model answered
(countdown_shared.openai.text), a short form of a notice published to everyone.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from countdown_shared.core.log_format import error_reason
from countdown_shared.notices import shortening
from countdown_shared.notices.shortening import NotShortError, StoredShort
from countdown_shared.openai.text import TextModelError, TextRefusedError
from countdown_shared.store import keys

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from redis.asyncio import Redis

    from countdown_shared.notices.shortening import Kind, Short
    from countdown_shared.openai.text import OpenAIText

logger = logging.getLogger(__name__)

# How many texts are newly asked about in one call: each takes a second or two of the provider's cycle, and costs.
NEW_PER_CYCLE = 5


class Shortener:
    """Keeps the store's short forms of a provider's texts up to date, through the model if there is one."""

    def __init__(
        self,
        redis: Redis,
        model: OpenAIText | None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Read and write through `redis` (the provider's own client); `model` is None with no OPENAI_API_KEY."""
        self.redis = redis
        self.model = model
        self.now = now

    async def ensure(self, kind: Kind, texts: Iterable[str]) -> int:
        """See that each of `texts` has its short form in the store; return how many were newly asked about.

        Does nothing without a model. Never raises.
        """
        if self.model is None:
            return 0
        wanted = {shortening.digest(kind, text): shortening.squash(text) for text in texts if text.strip()}
        if not wanted:
            return 0
        try:
            return await self._ensure(kind, wanted)
        except RedisError as error:
            logger.warning("Couldn't keep %d notices' short forms in the store: %s", len(wanted), error_reason(error))
            return 0

    async def shorts(self, kind: Kind, texts: Iterable[str]) -> dict[str, Short]:
        """Return the short form of each of `texts` that has one, by the text squashed, asking for new ones first.

        What a provider stores beside its texts. A text with none (not asked about yet, refused, or no model at all)
        is left out. Never raises.
        """
        wanted = {shortening.digest(kind, text): shortening.squash(text) for text in texts if text.strip()}
        if self.model is None or not wanted:
            return {}
        await self.ensure(kind, wanted.values())
        try:
            stored = await self.redis.mget([keys.notice_short(digest) for digest in wanted])
        except RedisError as error:
            logger.warning("Couldn't read %d notices' short forms from the store: %s", len(wanted), error_reason(error))
            return {}
        found: dict[str, Short] = {}
        for text, value in zip(wanted.values(), stored, strict=True):
            short = _short(value)
            if short is not None:
                found[text] = short
        return found

    async def _ensure(self, kind: Kind, wanted: dict[str, str]) -> int:
        digests = list(wanted)
        stored = await self.redis.mget([keys.notice_short(digest) for digest in digests])
        have = [digest for digest, value in zip(digests, stored, strict=True) if value is not None]
        missing = [digest for digest, value in zip(digests, stored, strict=True) if value is None]
        # An answer is renewed while its text is still being fetched. Not a failed one: it lapses after its hour, and
        # the text is asked about again.
        renew = [
            digest for digest, value in zip(digests, stored, strict=True) if value is not None and _is_short(value)
        ]
        if renew:
            async with self.redis.pipeline(transaction=False) as pipe:
                for digest in renew:
                    pipe.expire(keys.notice_short(digest), keys.NOTICE_SHORT_TTL_S)
                await pipe.execute()
        asked = 0
        for digest in missing[:NEW_PER_CYCLE]:
            made = await self._ask(kind, wanted[digest])
            if made is None:
                break
            asked += 1
            ttl = keys.NOTICE_SHORT_FAILED_TTL_S if made.failed else keys.NOTICE_SHORT_TTL_S
            await self.redis.set(keys.notice_short(digest), made.model_dump_json(), ex=ttl)
        if missing:
            logger.info(
                "Asked for the short form of %d of %d new notices (%d already in the store)",
                asked,
                len(missing),
                len(have),
            )
        return asked

    async def _ask(self, kind: Kind, text: str) -> StoredShort | None:
        """Return the model's short form of `text`, or that it has none; None if the model couldn't be asked."""
        model = self.model
        if model is None:
            return None
        try:
            # In a worker thread: the model's client is synchronous (requests).
            short = await asyncio.to_thread(shortening.shorten, model, kind, text)
        except (TextRefusedError, NotShortError) as error:
            # Its text is the model's status or the answer's lengths alone, never the notice's.
            logger.warning("%s gave a notice no short form: %s", model.name, error)
            return StoredShort(failed=True, model=model.name, made_at=self.now())
        except TextModelError as error:
            logger.warning("%s couldn't shorten a notice, so no more are asked this cycle: %s", model.name, error)
            return None
        return StoredShort(title=short.title, summary=short.summary, model=model.name, made_at=self.now())


def _short(value: str | bytes | None) -> Short | None:
    """Return a stored value's short form; None for none stored, one that failed, or another version's."""
    if value is None:
        return None
    try:
        return StoredShort.model_validate_json(value).short()
    except ValueError:
        return None


def _is_short(value: str | bytes) -> bool:
    """Whether a stored value is a short form to keep: not one that failed, nor another version's (left to lapse)."""
    return _short(value) is not None
