"""The render: one screen's frame, drawn from the store, run to completion.

render(device_id, now) reads the screen's config, sets its language and time zone, builds the clients fresh, has them
read their keys, draws the frame as of `now`, hashes it, writes it to the screen's record (`v1:frame:<device id>`,
store.frame_record) if the hash changed, and returns what happened (an Outcome). It has no sleeps, no loops beyond its
panels, starts no threads and keeps nothing between calls but the read-only caches every render shares: the fonts,
the translations and the Redis clients (reader.store). So the same inputs draw the same frame, whoever calls it.

Today each screen's own renderer calls it (core.display_loop: on the minute, on the broker's nudge, and every 20 s
while a provider asks for a faster refresh), and still PUTs the frame to the broker beside the record. A pool of
workers will call it from a job queue (docs/stateless-renderers.md, "The pool, the queue and the render function").
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfoNotFoundError

from redis.exceptions import RedisError

from countdown_core.core import moment
from countdown_core.core.api_registry import ApiRegistry
from countdown_core.core.targets import panel_bytes
from countdown_core.i18n import set_locale
from countdown_core.rendering import layout_for, to_native
from countdown_core.rendering import render as draw
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.screen import canvas_for
from countdown_core.store import config as stored_config
from countdown_core.store import frame_record, reader
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_credentials.registration import RendererRegistration

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from countdown_api.models import ConfigResponse
    from countdown_core.core.abstract_client import ClientStatus
    from countdown_core.core.panel import Panel

logger = logging.getLogger(__name__)

# The time zone for a config whose own the renderer doesn't know.
FALLBACK_TIMEZONE = "Europe/London"


class Result(StrEnum):
    """What became of a render's frame."""

    # A new picture: written to the screen's record.
    WRITTEN = auto()
    # The record holds this picture already, so nothing was written.
    UNCHANGED = auto()
    # Drawn, but not written: there's no store (no REDIS_URL), or Redis wouldn't take it (logged).
    UNRECORDED = auto()
    # Nothing to draw: the store has no config for the device.
    NO_CONFIG = auto()


class Stage(StrEnum):
    """What the screen shows, as the metrics report it (home_assistant.device_status.STAGES)."""

    PAIRING = "pairing"
    RUNNING = "running"


@dataclass(frozen=True)
class Outcome:
    """What a render did, for its caller: the frame, whether it was written, and what the metrics report.

    `panels`, `sections` and `locale` are what the next render needs to draw a failing client's last panel in its
    place (render's `previous`), if its caller keeps them.
    """

    result: Result
    stage: Stage | None = None
    # The picture as the panel takes it (the landscape picture, a portrait screen's turned a quarter), with its
    # volatile box; and its bytes, as the record and PUT /api/frame have them.
    frame: Frame | None = None
    data: bytes | None = None
    # The panel each API's client gave, or the one kept from `previous` for a client that failed; none for an API
    # whose client gave nothing.
    panels: Mapping[str, Panel] = field(default_factory=dict)
    # What each client was built with, and the language and time zone it drew in.
    sections: Mapping[str, object] = field(default_factory=dict)
    locale: tuple[str, str] | None = None
    # Where each client stood after it read (an API with no client isn't there), for the metrics.
    statuses: Mapping[str, ClientStatus] = field(default_factory=dict)
    # The APIs drawn "Not available" because their keys had expired.
    expired: frozenset[str] = frozenset()
    # Where the clients' providers can ask for a faster refresh (keys.pace).
    pace_keys: tuple[str, ...] = ()

    @property
    def etag(self) -> str | None:
        """The frame's hash (frame_record.frame_etag), as the record and the broker's ETag have it; None without one."""
        return None if self.data is None else frame_record.frame_etag(self.data)


def render(
    device_id: str,
    now: datetime,
    *,
    config: ConfigResponse | None = None,
    previous: Outcome | None = None,
) -> Outcome:
    """Draw the screen `device_id` as of `now` (aware), write its frame to its record if changed, and say how it went.

    The config is the broker's copy in the store (`v1:config:<device id>`), unless the caller has one already
    (`config`: today's per-screen renderer, which asks the broker when the store has none, and for a pairing code).
    With a pairing code it's the pairing screen. A client that fails other than by its key expiring draws its panel
    from `previous`, the last render's outcome, if that drew one with the same section of the config and in the same
    language and time zone; otherwise its area says it could not connect.

    Raises RedisError if the config can't be read from the store, and pydantic's ValidationError if what's there
    isn't a config. A record that can't be written is logged, and the frame still returned (Result.UNRECORDED).
    """
    with moment.drawing_at(now):
        if config is None:
            config = _stored_config(device_id)
            if config is None:
                return Outcome(Result.NO_CONFIG)
        locale = _apply_locale(config)
        if config.pairing_code:
            pairing = PairingCodePanel(config.pairing_code, device_id, has_changed=True)
            frame = to_native(Frame(pairing.render(*canvas_for(config.screen).size)), config.screen)
            data = bytes(panel_bytes(frame.image))
            return Outcome(_record(device_id, data), Stage.PAIRING, frame, data, locale=locale)

        # The clients only read the store, by the device's id: none of them calls the broker.
        registry = ApiRegistry(RendererRegistration(broker_url="", device_secret="", device_id=device_id), config)
        available = registry.available()
        registry.show_stops(layout_for(config.screen, available)[0].stops_shown)
        polled = registry.poll(_still_drawable(previous, registry, locale))
        drawn = draw(registry.panels(polled), config.screen, available, clock_format=config.clock_format, now=now)
        frame = to_native(drawn, config.screen)
        data = bytes(panel_bytes(frame.image))
        return Outcome(
            _record(device_id, data),
            Stage.RUNNING,
            frame,
            data,
            panels=polled,
            sections=registry.sections,
            locale=locale,
            statuses=registry.statuses(),
            expired=registry.expired(),
            pace_keys=tuple(registry.pace_keys()),
        )


def _stored_config(device_id: str) -> ConfigResponse | None:
    """Return the device's config from the store; None if there's none, or no store to read it from."""
    client = reader.store()
    return None if client is None else stored_config.read(client, device_id)


def _apply_locale(config: ConfigResponse) -> tuple[str, str]:
    """Set the config's language and time zone (a module global: one render at a time), and return them."""
    try:
        set_locale(config.language, config.timezone)
    except ZoneInfoNotFoundError, ValueError:
        logger.warning("Unknown time zone %r; using %s", config.timezone, FALLBACK_TIMEZONE)
        set_locale(config.language, FALLBACK_TIMEZONE)
    return config.language, config.timezone


def _still_drawable(previous: Outcome | None, registry: ApiRegistry, locale: tuple[str, str]) -> dict[str, Panel]:
    """Return the previous render's panels that are still this config's to draw, for a client that fails now.

    That's each one drawn in this language and time zone, whose client was built with a section of the config this
    render's wouldn't be rebuilt for (AbstractClient.needs_refresh): a panel from another config is never drawn.
    """
    if previous is None or previous.locale != locale:
        return {}
    return {
        name: panel
        for name, panel in previous.panels.items()
        if (client := registry.clients.get(name)) is not None
        and name in previous.sections
        and not client.needs_refresh(previous.sections[name])
    }


def _record(device_id: str, data: bytes) -> Result:
    """Write `data` to the screen's record unless it holds that picture already, and say which it was.

    A Redis that can't be asked is logged and left: the next render tries again. Nothing logged says what the frame
    shows.
    """
    client = reader.binary_store()
    if client is None:
        return Result.UNRECORDED
    try:
        written = frame_record.write_if_changed(client, device_id, data)
    except RedisError as e:
        logger.warning("Couldn't write the frame to the store (%s)", type(e).__name__)
        return Result.UNRECORDED
    return Result.WRITTEN if written else Result.UNCHANGED
