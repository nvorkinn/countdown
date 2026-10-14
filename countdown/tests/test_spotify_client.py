import asyncio
from datetime import timedelta

import pytest
import responses
from pydantic import ValidationError

from countdown_api.models import Spotify
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import SpotifyPanel
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
