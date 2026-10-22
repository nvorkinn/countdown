"""Rotation from the clock (core.rotation): every rotating panel shows page `epoch minute mod its page count`.

Deterministic: conftest's rotation_clock freezes the pages' clock, and these tests move it. The last one drives the
real render path, render.render as the pool's worker calls it, a minute at a time, through a config change.
"""

import datetime as dt
import json

import pytest
from config_factory import make_config
from conftest import ROTATION_START
from test_utils import TEST_DEVICE_ID, put_departures
from weather_factory import forecast_json

from countdown_core import render as render_module
from countdown_core.core import rotation
from countdown_core.core.api_registry import ApiRegistry
from countdown_core.store import keys
from countdown_core.tfl.combined_arrival_panel import Rotation
from countdown_core.weather.models import WeatherPeriod

TABS = [WeatherPeriod.NOW, WeatherPeriod.TONIGHT, WeatherPeriod.TOMORROW, WeatherPeriod.WEEKEND]
# Taken as this module is imported, before any test's fixtures freeze it.
REAL_NOW = rotation.now


# --- the page ---------------------------------------------------------------------------------------------------


def minute(n: int) -> dt.datetime:
    """Return ROTATION_START plus `n` minutes: a whole multiple of every count from 1 to 10 when n is 0."""
    return ROTATION_START + dt.timedelta(minutes=n)


@pytest.mark.parametrize("pages", [1, 2, 3, 4, 7, 8])
def test_the_page_is_the_minute_since_the_epoch_modulo_the_count(pages):
    assert [rotation.page(pages, minute(n)) for n in range(2 * pages)] == [n % pages for n in range(2 * pages)]


def test_panels_with_different_counts_each_go_by_their_own():
    at = minute(5)

    assert [rotation.page(pages, at) for pages in (2, 3, 4, 8)] == [1, 2, 1, 5]


def test_the_page_lasts_the_whole_minute():
    pages = [rotation.page(4, minute(1) + dt.timedelta(seconds=s)) for s in (0, 30, 59.999)]

    assert pages == [1, 1, 1]
    assert rotation.page(4, minute(2)) == 2


def test_the_count_carries_on_over_the_hour_rather_than_starting_again():
    """Seven pages don't divide 60: counted within the hour, 13:00 would jump back to page 0 after 12:59's page 3."""
    before = dt.datetime(2026, 1, 15, 12, 59, tzinfo=dt.UTC)
    after = before + dt.timedelta(minutes=1)

    assert rotation.page(7, after) == (rotation.page(7, before) + 1) % 7


def test_it_is_the_same_moment_in_any_time_zone():
    at = minute(3)

    assert rotation.page(4, at) == rotation.page(4, at.astimezone(dt.timezone(dt.timedelta(hours=-8)))) == 3


def test_without_a_time_it_goes_by_the_clock(rotation_clock):
    rotation_clock.at_minute(6)

    assert rotation.page(4) == 2


def test_the_clock_it_goes_by_is_the_real_time():
    """REAL_NOW: core.rotation's own clock, as it was before conftest's rotation_clock froze it for each test."""
    before = dt.datetime.now(dt.UTC)
    now = REAL_NOW()
    after = dt.datetime.now(dt.UTC)

    assert before <= now <= after
    assert now.tzinfo is not None


@pytest.mark.parametrize("pages", [0, -1])
def test_there_must_be_a_page_to_show(pages):
    with pytest.raises(ValueError, match="at least one page"):
        rotation.page(pages, minute(0))


# --- every rotating panel at once -------------------------------------------------------------------------------

DEVICE_ID = TEST_DEVICE_ID
STOPS = [f"49000000{i}" for i in range(5)]


def _fill(store) -> None:
    """Store what the providers would: five stops, London's forecast, the device's energy readings, its music."""
    for stop_id in STOPS:
        put_departures(store, stop_id, [("N155", "Somewhere", 300)], stop_letter="W")
    now = dt.datetime.now(dt.UTC)
    store.set(
        keys.weather("London"),
        json.dumps({"fetched_at": now.isoformat(), "location": "London", "forecast": forecast_json()}),
    )
    hourly = {"data": [[(now - dt.timedelta(hours=h)).timestamp(), 0.5] for h in range(1, 4)]}
    empty = {"data": []}
    energy = {"fetched_at": now.isoformat(), "device_id": DEVICE_ID, "offset": "0", "resource_id": "elec"}
    store.set(keys.energy(DEVICE_ID), json.dumps(energy | {"hourly": hourly, "daily": empty, "monthly": empty}))
    track = {"name": "A Song", "artists": [{"name": "Artist"}], "album": {"name": "Album", "images": []}}
    queue = {"currently_playing": track | {"duration_ms": 200_000}, "queue": []}
    lists = {"tracks": {"items": [track | {"duration_ms": 200_000}]}, "artists": {"items": [{"name": "Artist"}]}}
    store.set(keys.spotify(DEVICE_ID), json.dumps({"fetched_at": now.isoformat(), "spotify": queue}))
    store.set(
        keys.spotify_top(DEVICE_ID),
        json.dumps({"fetched_at": now.isoformat(), "spotify": {"long_term": lists, "short_term": lists}}),
    )


def _config(stops: list[str] = STOPS):
    return make_config(
        screen={"orientation": "horizontal", "width": 800, "height": 480, "format": "mono-1bpp-packed"},
        tfl={"stop_ids": stops},
        weather={"location": "London"},
        glowmarkt={"enabled": True},
        spotify={"enabled": True},
    )


def _pages(panels: dict) -> tuple:
    """Return the page each rotating panel is on: departures' rotation, the weather tab, energy's and Spotify's."""
    return (
        panels["tfl"].rotation,
        panels["weather"].weather.period,
        panels["glowmarkt"].page_index,
        type(panels["spotify"]).__name__,
    )


def test_every_rotating_panel_shows_its_own_page_for_the_minute(store, rotation_clock):
    """Five stops two at a time are 3 pages; the weather has 4 tabs, energy 3 pages, Spotify (one service) 4."""
    _fill(store)
    registry = ApiRegistry(TEST_DEVICE_ID, _config())
    registry.show_stops(2)
    rotation_clock.at_minute(5)

    panels = registry.poll()

    assert _pages(panels) == (Rotation(2, 3), WeatherPeriod.TONIGHT, 2, "SpotifyTopPanel")


# --- through the real render path -------------------------------------------------------------------------------


def test_the_render_turns_every_panel_a_page_a_minute_and_a_config_change_keeps_the_page(
    store, rotation_clock, monkeypatch, symbols_font
):
    """render.render, a minute apart, as the pool's ticker queues it: each render draws the minute's pages.

    After the second render the owner adds a stop. The next render's departures client, built afresh as every render's
    is, carries on at the clock's page (the third, of three still) rather than starting again at the first.
    """
    _fill(store)
    put_departures(store, "490000009", [("12", "Elsewhere", 60)], stop_letter="X")
    store.set(keys.config(DEVICE_ID), _config().model_dump_json())
    drawn = []
    real_render = render_module.draw

    def recording_render(panels, screen, available, *, clock_format="24h"):
        drawn.append(_pages(panels))
        return real_render(panels, screen, available, clock_format=clock_format)

    monkeypatch.setattr(render_module, "draw", recording_render)

    outcomes = []
    for minute in range(4):
        if minute == 2:
            store.set(keys.config(DEVICE_ID), _config([*STOPS, "490000009"]).model_dump_json())
        outcomes.append(render_module.render(DEVICE_ID, dt.datetime.now(dt.UTC)))
        rotation_clock.advance()

    assert drawn == [
        (Rotation(0, 3), TABS[0], 0, "SpotifyPanel"),
        (Rotation(1, 3), TABS[1], 1, "SpotifyTopPanel"),
        # A new departures client now, with six stops (still three pages): on the clock's page, not the first.
        (Rotation(2, 3), TABS[2], 2, "SpotifyPanel"),
        (Rotation(0, 3), TABS[3], 0, "SpotifyTopPanel"),
    ]
    # The last page of five stops has one; of six, the new stop is the second.
    assert len(outcomes[2].panels["tfl"].arrival_panels) == 2
