import asyncio
from datetime import timedelta

import pytest
import responses
from pydantic import ValidationError

from countdown_api.models import Spotify
from countdown_core.spotify.spotify_client import AppleMusicClient, SpotifyClient, music_client_class
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_styles import APPLE_MUSIC_LABELS, SPOTIFY_LABELS
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_credentials.registration import RendererRegistration

BROKER_URL = "https://broker.example.com"
QUEUE_URL = f"{BROKER_URL}/api/spotify/queue"
TOP_TRACKS_URL = f"{BROKER_URL}/api/spotify/top/tracks"
TOP_ARTISTS_URL = f"{BROKER_URL}/api/spotify/top/artists"


def track(name: str) -> dict:
    return {
        "name": name,
        "artists": [{"name": "An Artist"}, {"name": "Another Artist"}],
        "album": {"name": "An Album", "images": [{"width": 640, "height": 640, "url": "https://example.com/a.jpg"}]},
        "duration_ms": 180_000,
    }


QUEUE = {"currently_playing": track("A Song"), "queue": [track("Next Song")]}
NOTHING_PLAYING = {"currently_playing": None, "queue": [track("Next Song")]}


def mock_top():
    responses.add(responses.GET, TOP_TRACKS_URL, json={"items": [track("Top Song")]})
    responses.add(responses.GET, TOP_ARTISTS_URL, json={"items": [{"name": "Top Artist"}]})


@pytest.fixture
def client() -> SpotifyClient:
    return SpotifyClient(RendererRegistration(BROKER_URL, "shh", "device-123"), Spotify(enabled=True))


@responses.activate
def test_update_wraps_the_queue_in_a_panel(client):
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)

    panel = asyncio.run(client.update())

    assert isinstance(panel, SpotifyPanel)
    assert panel.queue.currently_playing.name == "A Song"
    assert panel.queue.currently_playing.duration_ms == timedelta(minutes=3)
    assert [t.name for t in panel.queue.queue] == ["Next Song"]


@responses.activate
def test_update_sends_the_device_secret_as_a_bearer_token(client):
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)

    asyncio.run(client.update())

    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer shh"
    assert request.body is None  # the secret goes in the header only


@responses.activate
def test_updates_rotate_between_the_player_and_the_top_tracks_and_artists(client):
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    mock_top()

    panels = [asyncio.run(client.update()) for _ in range(5)]

    assert [type(panel) for panel in panels] == [SpotifyPanel, SpotifyTopPanel] * 2 + [SpotifyPanel]
    assert [panels[1].period, panels[3].period] == ["12 months", "4 weeks"]
    time_ranges = [call.request.params for call in responses.calls if call.request.url.startswith(TOP_TRACKS_URL)]
    assert time_ranges == [{"time_range": "long_term", "limit": "5"}, {"time_range": "short_term", "limit": "5"}]


@responses.activate
def test_nothing_playing_skips_straight_to_the_top_tracks_and_artists(client):
    """Even with tracks still queued up: the player is about what's playing."""
    responses.add(responses.GET, QUEUE_URL, json=NOTHING_PLAYING)
    mock_top()

    panel = asyncio.run(client.update())

    assert isinstance(panel, SpotifyTopPanel)
    assert panel.period == "12 months"


@responses.activate
def test_update_raises_on_a_malformed_queue(client):
    responses.add(responses.GET, QUEUE_URL, json={"currently_playing": {"name": "missing everything else"}})

    with pytest.raises(ValidationError):
        asyncio.run(client.update())


@responses.activate
def test_the_configs_style_reaches_both_pages():
    client = SpotifyClient(RendererRegistration(BROKER_URL, "shh", "device-123"), Spotify(enabled=True, style="hero"))
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    mock_top()

    now_playing = asyncio.run(client.update())
    client.last_updated = None
    top = asyncio.run(client.update())

    assert isinstance(top, SpotifyTopPanel)
    assert (now_playing.style, top.style) == ("hero", "hero")


@responses.activate
def test_the_retired_classic_style_is_drawn_as_hero():
    """A broker that hasn't migrated its stored styles yet still sends classic."""
    client = SpotifyClient(
        RendererRegistration(BROKER_URL, "shh", "device-123"), Spotify(enabled=True, style="classic")
    )
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    mock_top()

    now_playing = asyncio.run(client.update())
    client.last_updated = None
    top = asyncio.run(client.update())

    assert (now_playing.style, top.style) == ("hero", "hero")


def test_the_source_picks_the_client():
    assert music_client_class(Spotify(enabled=True)) is SpotifyClient
    assert music_client_class(Spotify(enabled=True).model_copy(update={"source": "apple_music"})) is AppleMusicClient


@responses.activate
def test_apple_music_reads_the_brokers_apple_music_paths_and_labels_the_last_played():
    apple = f"{BROKER_URL}/api/apple-music"
    responses.add(responses.GET, f"{apple}/queue", json=QUEUE)
    responses.add(responses.GET, f"{apple}/top/tracks", json={"items": [track("Top Song")]})
    responses.add(responses.GET, f"{apple}/top/artists", json={"items": [{"name": "Top Artist"}]})
    client = AppleMusicClient(RendererRegistration(BROKER_URL, "shh", "device-123"), Spotify(enabled=True))

    now, top = asyncio.run(client.update()), asyncio.run(client.update())

    assert now.labels is APPLE_MUSIC_LABELS
    assert [t.name for t in top.tracks] == ["Top Song"]
    assert all(call.request.url.startswith(apple) for call in responses.calls)


@responses.activate
def test_spotify_labels_its_player_as_now_playing(client):
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)

    assert asyncio.run(client.update()).labels is SPOTIFY_LABELS


@responses.activate
def test_apple_music_with_nothing_played_says_so_under_its_name():
    apple = f"{BROKER_URL}/api/apple-music"
    responses.add(responses.GET, f"{apple}/queue", json={"currently_playing": None, "queue": []})
    responses.add(responses.GET, f"{apple}/top/tracks", json={"items": []})
    responses.add(responses.GET, f"{apple}/top/artists", json={"items": []})
    client = AppleMusicClient(RendererRegistration(BROKER_URL, "shh", "device-123"), Spotify(enabled=True))

    panel = asyncio.run(client.update())

    assert panel.title == "Apple Music"
    assert panel.logo is None


@responses.activate
def test_empty_top_lists_give_way_to_the_player(client):
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    responses.add(responses.GET, TOP_TRACKS_URL, json={"items": []})
    responses.add(responses.GET, TOP_ARTISTS_URL, json={"items": []})
    asyncio.run(client.update())

    assert isinstance(asyncio.run(client.update()), SpotifyPanel)
