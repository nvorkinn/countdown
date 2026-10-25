"""The render: one screen's frame, drawn from the store, run to completion.

render(device_id, now) reads the screen's config, sets its language and time zone, builds the clients fresh, has them
read their keys, draws the frame as of `now`, hashes it, writes it to the screen's record (`v1:frame:<device id>`,
store.frame_record) if the hash changed, and returns what happened (an Outcome). It has no sleeps, no loops beyond its
panels, starts no threads and keeps nothing between calls but the read-only caches every render shares: the fonts,
the translations and the Redis clients (reader.store). So the same inputs draw the same frame, whoever calls it.

The render pool's workers (countdown_renderer.render_pool) call it from a job queue (store.render_queue), for every
screen the broker lists in `v1:render:screens` (see countdown/docs/stateless-renderers.md). The broker serves the
record to the screen.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfoNotFoundError

from redis.exceptions import RedisError

from countdown_core.core import moment
from countdown_core.core.api_registry import ApiRegistry, ClientClasses
from countdown_core.i18n import set_locale
from countdown_core.rendering import layout_for, stops_fitting, to_native
from countdown_core.rendering import render as draw
from countdown_core.rendering.encoding import encode
from countdown_core.rendering.layout import Frame
from countdown_core.rendering.screen import canvas_for
from countdown_core.store import config as stored_config
from countdown_core.store import frame_record, reader
from countdown_core.store import pairing as stored_pairing
from countdown_core.system_screens.pairing_code_panel import PairingCodePanel
from countdown_core.utils.scaled_draw import drawing_at

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from PIL import Image

    from countdown_api.models import ConfigResponse
    from countdown_core.core.abstract_client import ClientStatus
    from countdown_core.core.panel import Panel
    from countdown_core.rendering.screen import Canvas

logger = logging.getLogger(__name__)

# The time zone for a config whose own the renderer doesn't know.
FALLBACK_TIMEZONE = "Europe/London"

# The 7.5" screens' short side; a screen with a longer one is HD, and draws the pairing screen bigger.
DESIGN_SHORT_SIDE = 480
# The pairing screen's design width, landscape and portrait (800x480 and 480x800).
PAIRING_DESIGN_WIDTH = {True: 800, False: 480}


class Result(StrEnum):
    """What became of a render's frame."""

    # A new picture: written to the screen's record.
    WRITTEN = auto()
    # The record holds this picture already, so nothing was written.
    UNCHANGED = auto()
    # Drawn, but not written: Redis wouldn't take it (logged).
    UNRECORDED = auto()
    # Nothing to draw: the store has no config for the device (or there's no store: no REDIS_URL).
    NO_CONFIG = auto()


class Stage(StrEnum):
    """What the screen shows, as the metrics report it (countdown.stage's `stage`)."""

    PAIRING = "pairing"
    RUNNING = "running"


@dataclass(frozen=True)
class Outcome:
    """What a render did, for its caller: the frame, whether it was written, and what the metrics report."""

    result: Result
    stage: Stage | None = None
    # The picture as the panel takes it (the landscape picture, a portrait screen's turned a quarter); and its bytes,
    # as the record has them.
    frame: Frame | None = None
    data: bytes | None = None
    # The panel each API's client gave; none for an API whose client gave nothing (its area says it could not
    # connect).
    panels: Mapping[str, Panel | None] = field(default_factory=dict)
    # Where each client stood after it read (an API with no client isn't there), for the metrics.
    statuses: Mapping[str, ClientStatus] = field(default_factory=dict)
    # The APIs drawn "Not available" because their keys had expired.
    expired: frozenset[str] = frozenset()

    @property
    def etag(self) -> str | None:
        """The frame's hash (frame_record.frame_etag), as the record and the broker's ETag have it; None without one."""
        return None if self.data is None else frame_record.frame_etag(self.data)


def render(device_id: str, now: datetime) -> Outcome:
    """Draw the screen `device_id` as of `now` (aware), write its frame to its record if changed, and say how it went.

    The config is the broker's copy in the store (`v1:config:<device id>`). While the broker has a pairing code for
    the screen to show, it's the pairing screen: the code is the broker's `v1:pairing:<device id>` (store.pairing),
    which while it's there is the config to draw.

    A client that can't ask Redis raises its RedisError, and nothing is written: the screen keeps the frame it has,
    rather than one saying it could not connect. A key that has expired still draws "Not available": that's the
    data's expiry, not Redis failing. A client that fails any other way (a bug) gives no panel, and its area says it
    could not connect.

    Raises RedisError if the config (or the code) can't be read from the store, and pydantic's ValidationError if
    what's there isn't a config. A record that can't be written is logged, and the frame still returned
    (Result.UNRECORDED).
    """
    with moment.drawing_at(now):
        config = _live_pairing(device_id) or _stored_config(device_id)
        if config is None:
            return Outcome(Result.NO_CONFIG)
        _apply_locale(config)
        if config.pairing_code:
            pairing = PairingCodePanel(config.pairing_code, device_id)
            frame = to_native(Frame(_pairing_picture(pairing, canvas_for(config.screen))), config.screen)
            data = encode(frame.image, config.screen)
            return Outcome(_record(device_id, data), Stage.PAIRING, frame, data)

        # The clients only read the store, by the device's id: none of them calls the broker.
        registry = ApiRegistry(device_id, config)
        available = registry.available()
        stops_shown = layout_for(config.screen, available)[0].stops_shown
        registry.show_stops(stops_shown)
        polled = registry.poll()
        fitting = stops_fitting(registry.panels(polled), config.screen, available)
        if fitting < stops_shown:
            # The notices took the second row's room: read this minute's page of stops again, a row of them.
            registry.show_stops(fitting)
            polled |= registry.poll(only=ClientClasses.TFL)
        drawn = draw(registry.panels(polled), config.screen, available, clock_format=config.clock_format)
        frame = to_native(drawn, config.screen)
        data = encode(frame.image, config.screen)
        return Outcome(
            _record(device_id, data),
            Stage.RUNNING,
            frame,
            data,
            panels=polled,
            statuses=registry.statuses(),
            expired=registry.expired(),
        )


def _stored_config(device_id: str) -> ConfigResponse | None:
    """Return the device's config from the store; None if there's none, or no store to read it from."""
    client = reader.store()
    return None if client is None else stored_config.read(client, device_id)


def _pairing_picture(pairing: PairingCodePanel, canvas: Canvas) -> Image.Image:
    """Draw the pairing screen for `canvas`: in the 7.5" screens' pixels, `scale` times as big on the HD screen."""
    landscape = canvas.width > canvas.height
    scale = canvas.width / PAIRING_DESIGN_WIDTH[landscape] if min(canvas.size) > DESIGN_SHORT_SIDE else 1.0
    with drawing_at(scale):
        return pairing.render(round(canvas.width / scale), round(canvas.height / scale))


def _live_pairing(device_id: str) -> ConfigResponse | None:
    """Return the device's config with the pairing code it's to show, from the store; None without a live code.

    The store's config never carries the code, so this is how a render that reads the store shows it.
    """
    client = reader.store()
    return None if client is None else stored_pairing.read(client, device_id)


def _apply_locale(config: ConfigResponse) -> None:
    """Set the config's language and time zone: a module global, so a worker runs one render at a time."""
    try:
        set_locale(config.language, config.timezone)
    except ZoneInfoNotFoundError, ValueError:
        logger.warning("Unknown time zone %r; using %s", config.timezone, FALLBACK_TIMEZONE)
        set_locale(config.language, FALLBACK_TIMEZONE)


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
