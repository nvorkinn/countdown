"""The background provider: the weather panel's AI-made photos, one per part of each place's day, into the store.

Its ids are the weather keys in `v1:sub:background`: the places the paired touchscreens show, which countdown-server
publishes with, beside each, what the key doesn't say (`v1:background-place:<weather key>`: its name for the prompt,
its country and its notice boards' postcodes). An e-paper screen draws no background, so its place isn't there and
costs nothing.

Every INTERVAL_S, for each place, it works out from the stored forecast which part of the day is due (logic.py: each is
made LEAD before it starts). One not made yet is made: dawn new, each later part relit from the one before it, by the
first model that answers (countdown_shared.openai.images: OpenAI's), else the curated picture with `fallback: true`,
which is tried again after logic.RETRY. Its bytes go to `v1:background-image:<sha256>`, written here through a second
client that answers bytes (the runner's decodes), and the day's list to `v1:background:<weather key>`, which the runner
writes after: so the list never names a picture that isn't there. Both are the store contract's, kept two days.

The day's list starts again at the place's dawn, so until then screens keep the night before's. A place with no
forecast stored (its provider is down, or new), or none of its own yet under `v1:background-place:`, waits for one. A
picture takes about a minute, so a place is a batch of its own (batch_size 1): each is written as it's made, and a
stop is noticed between places. A cycle with several places due takes a few minutes; the next one simply starts late.

Nothing logged names a place: a weather key is a town, or typed text that can be a postcode.
"""

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from countdown_api.models import (
    BackgroundPlace,
    StoredBackground,
    StoredBankHolidays,
    StoredForecast,
    StoredMetOfficeWarnings,
)
from countdown_providers.background import logic
from countdown_providers.background.logic import Context, Place
from countdown_providers.config import Settings
from countdown_providers.runner import serve
from countdown_shared.core.log_format import configure_logging
from countdown_shared.openai import images
from countdown_shared.openai.images import ImageModel, ImageModelError
from countdown_shared.store import keys

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime

    from redis.asyncio import Redis

    from countdown_api.models import BackgroundPhase

logger = logging.getLogger(__name__)

# Often enough that a part of the day is made well inside logic.LEAD of its start.
INTERVAL_S = 300


class BackgroundProvider:
    """Makes each place's due background, a place at a time."""

    name = keys.BACKGROUND
    # Renewed with each picture written to the day's list.
    ttl_s = keys.BACKGROUND_TTL_S
    interval_s = INTERVAL_S
    # A place at a time: its picture takes about a minute, and is written (and a stop noticed) before the next's.
    batch_size = 1

    def __init__(
        self,
        redis: Redis,
        pictures: Redis,
        models: list[ImageModel],
        now: Callable[[], datetime] = logic.utc_now,
    ) -> None:
        """Read the store through `redis` (the runner's, which decodes) and the pictures' bytes through `pictures`.

        `models` are those to try in order (images.configured()); with none, every picture is the curated one.
        """
        self.redis = redis
        self.pictures = pictures
        self.models = models
        self.now = now

    def output_key(self, item_id: str) -> str:
        """Write a place's day of backgrounds to `v1:background:<weather key>`."""
        return keys.background(item_id)

    async def fetch(self, ids: list[str]) -> dict[str, StoredBackground]:
        """Make the due background of each of `ids` (weather keys) that hasn't one; return the days that gained one.

        A place with nothing due is left out, so its stored day stands as it is. Raises RedisError.
        """
        made: dict[str, StoredBackground] = {}
        for weather_key in ids:
            day = await self._make_due(weather_key)
            if day is not None:
                made[weather_key] = day
        return made

    async def _make_due(self, weather_key: str) -> StoredBackground | None:
        """Make the place's due background if it hasn't one yet; return its day with it, or None with nothing made."""
        its_place, its_forecast, its_day = await self.redis.mget(
            [keys.background_place(weather_key), keys.weather(weather_key), keys.background(weather_key)]
        )
        stored_place = _read(its_place, BackgroundPlace)
        forecast = _read(its_forecast, StoredForecast)
        if stored_place is None or forecast is None:
            return None
        place = Place(weather_key, stored_place.name, stored_place.country, frozenset(stored_place.postcodes))
        now = self.now()
        day = logic.day_of(forecast, now)
        weather = None
        phase = logic.due(day, now) if day else None
        if day is not None and phase is not None:
            weather = logic.weather_at(forecast, day, phase)
        if day is None or phase is None or weather is None:
            return None
        stored = _json(its_day)
        images_so_far = stored["images"] if stored and stored.get("local_date") == day.local_date.isoformat() else []
        if logic.next_step(images_so_far, phase, now) == "keep":
            return None
        context = Context(
            local_date=day.local_date,
            weather=weather,
            occasions=logic.occasions(day.local_date),
            bank_holiday=await self._bank_holiday(place, day.local_date),
            warnings=await self._warnings(place),
        )
        prompt = logic.prompt(place, phase, context)
        source = logic.reference(images_so_far, phase)
        picture, fallback = await self._make(phase, prompt, source)
        digest = hashlib.sha256(picture).hexdigest()
        image = {
            "phase": phase,
            "starts_at": logic.iso_minute(day.starts[phase]),
            "digest": digest,
            "made_at": logic.iso_utc(now),
            "fallback": fallback,
        }
        # The picture first: the runner writes the list that names it once this returns.
        await self.pictures.set(keys.background_image(digest), picture, ex=keys.BACKGROUND_IMAGE_TTL_S)
        logger.info("Made a %s background%s", phase, " (a fallback)" if fallback else "")
        return StoredBackground.model_validate(
            {
                "location": weather_key,
                "local_date": day.local_date.isoformat(),
                "images": logic.with_image(images_so_far, image),
            }
        )

    async def _make(self, phase: BackgroundPhase, prompt: str, source: dict[str, Any] | None) -> tuple[bytes, bool]:
        """Return the picture and whether it's the fallback: the first model's that answers, else the curated one."""
        reference = await self.pictures.get(keys.background_image(source["digest"])) if source else None
        for model in self.models:
            try:
                if isinstance(reference, bytes):
                    # In a worker thread: the model's client is synchronous (requests), and takes about a minute.
                    return await asyncio.to_thread(model.make, logic.RELIGHT + prompt, reference), False
                return await asyncio.to_thread(model.make, prompt), False
            except ImageModelError as error:
                # Its text is the model's status and error code alone (countdown_shared.openai.http.error_code).
                logger.warning("%s made no %s background: %s", model.name, phase, error)
        return logic.fallback(phase), True

    async def _bank_holiday(self, place: Place, day: date) -> str | None:
        if place.country != "GB":
            return None
        return logic.bank_holiday(_read(await self.redis.get(keys.BANK_HOLIDAYS), StoredBankHolidays), day)

    async def _warnings(self, place: Place) -> list[str]:
        if place.country != "GB" or not place.postcodes:
            return []
        values = await self.redis.mget([keys.met_office(postcode) for postcode in sorted(place.postcodes)])
        feeds = [_read(value, StoredMetOfficeWarnings) for value in values]
        return logic.warnings([feed for feed in feeds if feed is not None])


def _json(value: str | bytes | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _read[M: BaseModel](value: str | bytes | None, model: type[M]) -> M | None:
    """Return a stored value as the contract's `model`; None if it's missing or the model won't read it."""
    if value is None:
        return None
    try:
        return model.model_validate_json(value)
    except ValidationError as error:
        # Not its key, which names the place, nor the error's text, which quotes the value.
        logger.warning("A stored value isn't the contract's %s (%d errors)", model.__name__, error.error_count())
        return None


async def _serve(settings: Settings, models: list[ImageModel]) -> None:
    """Run the provider until SIGINT or SIGTERM, on two clients: the runner's, and one answering the pictures' bytes."""
    redis = settings.redis()
    pictures = settings.redis(decode_responses=False)
    try:
        await serve(BackgroundProvider(redis, pictures, models), settings, redis=redis)
    finally:
        await pictures.aclose()


def main() -> None:
    """Run the `countdown-provider-background` console script; OPENAI_API_KEY is optional (the curated pictures)."""
    configure_logging()
    models = images.configured()
    if not models:
        logger.warning("No OPENAI_API_KEY: the backgrounds are the curated ones")
    asyncio.run(_serve(Settings.from_env(), models))


if __name__ == "__main__":
    main()
