"""The Spotify area's client, reading what the broker's music provider stored (test_spotify_store has the contract)."""

import asyncio
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_utils import TEST_DEVICE_ID

from countdown_core.core import rotation
from countdown_core.spotify.spotify_client import (
    TOP_WHEN_IDLE_EVERY_S,
    AppleMusicClient,
    MusicSettings,
    SpotifyClient,
    music_client_class,
)
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_styles import APPLE_MUSIC_LABELS, SPOTIFY_LABELS
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.store import keys, reader

DEVICE_ID = TEST_DEVICE_ID


def track(name: str) -> dict:
    return {
        "id": name,
        "name": name,
        "artists": [{"name": "An Artist"}, {"name": "Another Artist"}],
        "album": {"name": "An Album", "images": [{"width": 640, "height": 640, "url": "https://example.com/a.jpg"}]},
        "duration_ms": 180_000,
    }


QUEUE = {"currently_playing": track("A Song"), "queue": [track("Next Song")]}
NOTHING_PLAYING = {"currently_playing": None, "queue": [track("Next Song")]}
SILENT = {"currently_playing": None, "queue": []}


def top(*tracks: str, artist: str = "Top Artist") -> dict:
    """Return a service's top lists, the same for both periods: the tracks named, and `artist` if there are any."""
    lists = {
        "tracks": {"items": [track(name) for name in tracks]},
        "artists": {"items": [{"name": artist}] * bool(tracks)},
    }
    return {"long_term": lists, "short_term": lists}


SPOTIFY_TOP = top("Top Song")
APPLE_TOP = top("Apple Top Song", artist="Apple Artist")


def put(  # noqa: PLR0913 -- a service's player and top lists each, and whether Spotify plays
    store, *, spotify=None, spotify_top=None, apple_music=None, apple_music_top=None, playing=True
) -> None:
    """Store the device's player and top lists as the music provider does; a service left out is None.

    Spotify's track, if it has one, is `playing` as of now (rotation.now, which a test may have frozen).
    """
    fetched_at = rotation.now().isoformat()
    player = {"fetched_at": fetched_at, "spotify": spotify, "apple_music": apple_music}
    if spotify and spotify["currently_playing"]:
        playback = {"fetched_at": fetched_at, "is_playing": playing, "progress_ms": 0}
        player["spotify_playback"] = playback | {"track_id": spotify["currently_playing"].get("id")}
    top_lists = {"fetched_at": fetched_at, "spotify": spotify_top, "apple_music": apple_music_top}
    store.set(keys.spotify(DEVICE_ID), json.dumps(player))
    store.set(keys.spotify_top(DEVICE_ID), json.dumps(top_lists))


@pytest.fixture
def client() -> SpotifyClient:
    return SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))


def test_update_wraps_the_queue_in_a_panel(store, client):
    put(store, spotify=QUEUE)

    panel = asyncio.run(client.update())

    assert isinstance(panel, SpotifyPanel)
    assert panel.queue.currently_playing.name == "A Song"
    assert panel.queue.currently_playing.duration_ms == timedelta(minutes=3)
    assert [t.name for t in panel.queue.queue] == ["Next Song"]


def turn(client: SpotifyClient, rotation_clock, times: int) -> list:
    """Update `times` times, a minute apart, as the registry polls: a page a minute (core.rotation)."""
    panels = []
    for minute in range(times):
        rotation_clock.at_minute(minute)
        panels.append(asyncio.run(client.update()))
    return panels


def visits(client: SpotifyClient, rotation_clock, times: int) -> list:
    """Update at the start of `times` visits in a row while nothing plays (TOP_WHEN_IDLE_EVERY_S apart)."""
    panels = []
    for visit in range(times):
        rotation_clock.at_minute(visit * TOP_WHEN_IDLE_EVERY_S // 60)
        panels.append(asyncio.run(client.update()))
    return panels


def test_while_playing_the_player_stays_on_minute_after_minute(store, client, rotation_clock):
    panels = []
    for minute in range(4):
        rotation_clock.at_minute(minute)
        put(store, spotify=QUEUE, spotify_top=SPOTIFY_TOP)  # the provider reads the position again and again
        panels.append(asyncio.run(client.update()))

    assert [type(panel) for panel in panels] == [SpotifyPanel] * 4


def test_while_nothing_plays_the_top_lists_visit_in_turn(store, client, rotation_clock):
    long_term = top("Long Song")["long_term"]
    put(store, spotify=NOTHING_PLAYING, spotify_top={"long_term": long_term, "short_term": SPOTIFY_TOP["short_term"]})

    panels = visits(client, rotation_clock, 3)

    assert [panel.period for panel in panels] == ["12 months", "4 weeks", "12 months"]
    assert [panel.tracks[0].name for panel in panels] == ["Long Song", "Top Song", "Long Song"]


def test_a_paused_track_is_nothing_playing(store, client, rotation_clock):
    put(store, spotify=QUEUE, spotify_top=SPOTIFY_TOP, playing=False)

    assert isinstance(asyncio.run(client.update()), SpotifyTopPanel)
    assert client.is_idle() is False  # a visit's minute
    rotation_clock.advance()
    assert client.is_idle() is True


def test_nothing_playing_skips_straight_to_the_top_tracks_and_artists(store, client, rotation_clock):
    """Even with tracks still queued up: the player is about what's playing."""
    put(store, spotify=NOTHING_PLAYING, spotify_top=SPOTIFY_TOP)

    panel = asyncio.run(client.update())

    assert isinstance(panel, SpotifyTopPanel)
    assert panel.period == "12 months"


def test_update_raises_on_a_malformed_queue(store, client):
    put(store, spotify={"currently_playing": {"name": "missing everything else"}})

    with pytest.raises(ValidationError):
        asyncio.run(client.update())


@pytest.mark.parametrize(("style", "drawn"), [("hero", "hero"), ("classic", "hero")], ids=["hero", "retired classic"])
def test_the_configs_style_reaches_both_pages(store, style, drawn, rotation_clock):
    """The broker may still send classic, which draws as hero."""
    client = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True, style=style))
    put(store, spotify=QUEUE, spotify_top=SPOTIFY_TOP)
    now_playing = asyncio.run(client.update())
    put(store, spotify=QUEUE, spotify_top=SPOTIFY_TOP, playing=False)
    top_page = asyncio.run(client.update())

    assert isinstance(top_page, SpotifyTopPanel)
    assert (now_playing.style, top_page.style) == (drawn, drawn)


def test_the_services_pick_the_client():
    assert music_client_class(MusicSettings(spotify=True)) is SpotifyClient
    assert music_client_class(MusicSettings(spotify=True, apple_music=True)) is SpotifyClient
    assert music_client_class(MusicSettings(spotify=False, apple_music=True)) is AppleMusicClient


def music_client(*, spotify=True, apple_music=True) -> SpotifyClient:
    settings = MusicSettings(spotify=spotify, apple_music=apple_music)
    return music_client_class(settings)(TEST_DEVICE_ID, settings)


def test_apple_music_alone_reads_its_part_of_the_store_and_labels_the_last_played(store, rotation_clock):
    put(store, apple_music=QUEUE, apple_music_top=APPLE_TOP)
    client = music_client(spotify=False)

    now, top_page = visits(client, rotation_clock, 2)

    assert now.labels is APPLE_MUSIC_LABELS
    assert [t.name for t in top_page.tracks] == ["Apple Top Song"]
    assert top_page.service is None  # only one service: no need to say whose


def test_with_both_spotify_shows_while_it_is_playing(store, rotation_clock):
    put(store, spotify=QUEUE, apple_music={"currently_playing": track("Apple Song"), "queue": []})

    panel = asyncio.run(music_client().update())

    assert panel.labels is SPOTIFY_LABELS
    assert panel.queue.currently_playing.name == "A Song"


def test_with_both_and_spotify_idle_apple_musics_last_played_visits(store, rotation_clock):
    put(store, spotify=NOTHING_PLAYING, apple_music={"currently_playing": track("Apple Song"), "queue": []})

    panel = asyncio.run(music_client().update())

    assert panel.labels is APPLE_MUSIC_LABELS
    assert panel.queue.currently_playing.name == "Apple Song"


def test_with_both_the_top_lists_take_turns_and_say_whose_they_are(store, rotation_clock):
    put(store, spotify=NOTHING_PLAYING, spotify_top=SPOTIFY_TOP, apple_music=QUEUE, apple_music_top=APPLE_TOP)
    client = music_client()

    panels = visits(client, rotation_clock, 8)

    tops = [(p.service, p.period, p.tracks[0].name) for p in panels if isinstance(p, SpotifyTopPanel)]
    assert tops == [
        ("Spotify", "12 months", "Top Song"),
        ("Apple Music", "12 months", "Apple Top Song"),
        ("Spotify", "4 weeks", "Top Song"),
        ("Apple Music", "4 weeks", "Apple Top Song"),
    ]


def test_with_both_and_nothing_anywhere_it_says_so(store):
    put(store, spotify=NOTHING_PLAYING, spotify_top=top(), apple_music=SILENT, apple_music_top=top())

    panel = asyncio.run(music_client().update())

    assert panel.message == "Nothing playing on:"


def test_apple_music_alone_with_nothing_played_says_so_under_its_name(store):
    put(store, apple_music=SILENT, apple_music_top=top())

    panel = asyncio.run(music_client(spotify=False).update())

    assert panel.title == "Apple Music"
    assert panel.logo is None


def test_spotify_labels_its_player_as_now_playing(store, client, rotation_clock):
    put(store, spotify=QUEUE)

    assert asyncio.run(client.update()).labels is SPOTIFY_LABELS


def test_the_area_is_folded_away_only_while_nothing_plays_and_nothing_visits(store, client, rotation_clock):
    put(store, spotify=QUEUE, spotify_top=SPOTIFY_TOP)
    assert client.is_idle() is False
    put(store, spotify=NOTHING_PLAYING, spotify_top=SPOTIFY_TOP)
    assert client.is_idle() is False  # a visit's minute
    rotation_clock.at_minute(1)
    assert client.is_idle() is True
    rotation_clock.at_minute(TOP_WHEN_IDLE_EVERY_S // 60)
    assert client.is_idle() is False


def test_a_visit_with_nothing_to_show_stays_folded_away(store, client, rotation_clock):
    put(store, spotify=NOTHING_PLAYING, spotify_top=top())

    assert client.is_idle() is True


def test_with_nothing_in_the_store_the_area_is_folded_away(store, client, rotation_clock):
    assert client.is_idle() is True


def test_the_same_minute_shows_the_same_page(store, client, rotation_clock):
    put(store, spotify=NOTHING_PLAYING, spotify_top=SPOTIFY_TOP)

    first, again = asyncio.run(client.update()), asyncio.run(client.update())

    assert isinstance(first, SpotifyTopPanel)
    assert isinstance(again, SpotifyTopPanel)
    assert first.period == again.period == "12 months"


def test_without_a_store_it_cant_be_built_and_says_why(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)

    with pytest.raises(reader.StoreNotConfiguredError, match="REDIS_URL"):
        SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))


def test_it_never_calls_the_broker(store, client, monkeypatch, rotation_clock):
    """The provider calls the services with the broker's tokens; the client only reads what it stored."""
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: pytest.fail("called the broker"))
    put(store, spotify=QUEUE, spotify_top=SPOTIFY_TOP)
    playing = asyncio.run(client.update())
    put(store, spotify=NOTHING_PLAYING, spotify_top=SPOTIFY_TOP)

    assert [type(playing), type(asyncio.run(client.update()))] == [SpotifyPanel, SpotifyTopPanel]
