import asyncio
from datetime import timedelta

import pytest
import responses
from pydantic import ValidationError

from countdown_core.spotify.spotify_client import AppleMusicClient, MusicSettings, SpotifyClient, music_client_class
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
    return SpotifyClient(RendererRegistration(BROKER_URL, "shh", "device-123"), MusicSettings(spotify=True))


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
    client = SpotifyClient(
        RendererRegistration(BROKER_URL, "shh", "device-123"), MusicSettings(spotify=True, style="hero")
    )
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
        RendererRegistration(BROKER_URL, "shh", "device-123"), MusicSettings(spotify=True, style="classic")
    )
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    mock_top()

    now_playing = asyncio.run(client.update())
    client.last_updated = None
    top = asyncio.run(client.update())

    assert (now_playing.style, top.style) == ("hero", "hero")


def test_the_services_pick_the_client():
    assert music_client_class(MusicSettings(spotify=True)) is SpotifyClient
    assert music_client_class(MusicSettings(spotify=True, apple_music=True)) is SpotifyClient
    assert music_client_class(MusicSettings(spotify=False, apple_music=True)) is AppleMusicClient


APPLE = f"{BROKER_URL}/api/apple-music"


def mock_apple(queue=QUEUE, tracks=("Apple Top Song",)):
    responses.add(responses.GET, f"{APPLE}/queue", json=queue)
    responses.add(responses.GET, f"{APPLE}/top/tracks", json={"items": [track(name) for name in tracks]})
    responses.add(responses.GET, f"{APPLE}/top/artists", json={"items": [{"name": "Apple Artist"}] if tracks else []})


def music_client(*, spotify=True, apple_music=True) -> SpotifyClient:
    settings = MusicSettings(spotify=spotify, apple_music=apple_music)
    return music_client_class(settings)(RendererRegistration(BROKER_URL, "shh", "device-123"), settings)


@responses.activate
def test_apple_music_alone_reads_the_brokers_apple_music_paths_and_labels_the_last_played():
    mock_apple()
    client = music_client(spotify=False)

    now, top = asyncio.run(client.update()), asyncio.run(client.update())

    assert now.labels is APPLE_MUSIC_LABELS
    assert [t.name for t in top.tracks] == ["Apple Top Song"]
    assert top.service is None  # only one service: no need to say whose
    assert all(call.request.url.startswith(APPLE) for call in responses.calls)


@responses.activate
def test_with_both_spotify_shows_while_it_is_playing():
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    mock_apple()

    panel = asyncio.run(music_client().update())

    assert panel.labels is SPOTIFY_LABELS
    assert not any(call.request.url.startswith(APPLE) for call in responses.calls)


@responses.activate
def test_with_both_and_spotify_idle_apple_musics_last_played_shows():
    responses.add(responses.GET, QUEUE_URL, json=NOTHING_PLAYING)
    mock_apple(queue={"currently_playing": track("Apple Song"), "queue": []})

    panel = asyncio.run(music_client().update())

    assert panel.labels is APPLE_MUSIC_LABELS
    assert panel.queue.currently_playing.name == "Apple Song"


@responses.activate
def test_with_both_the_top_lists_take_turns_and_say_whose_they_are():
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    mock_top()
    mock_apple()
    client = music_client()

    panels = [asyncio.run(client.update()) for _ in range(8)]

    tops = [(p.service, p.period, p.tracks[0].name) for p in panels if isinstance(p, SpotifyTopPanel)]
    assert tops == [
        ("Spotify", "12 months", "Top Song"),
        ("Apple Music", "12 months", "Apple Top Song"),
        ("Spotify", "4 weeks", "Top Song"),
        ("Apple Music", "4 weeks", "Apple Top Song"),
    ]


@responses.activate
def test_with_both_and_nothing_anywhere_it_says_so():
    responses.add(responses.GET, QUEUE_URL, json=NOTHING_PLAYING)
    responses.add(responses.GET, TOP_TRACKS_URL, json={"items": []})
    responses.add(responses.GET, TOP_ARTISTS_URL, json={"items": []})
    mock_apple(queue={"currently_playing": None, "queue": []}, tracks=())

    panel = asyncio.run(music_client().update())

    assert panel.message == "Nothing playing on:"


@responses.activate
def test_apple_music_alone_with_nothing_played_says_so_under_its_name():
    mock_apple(queue={"currently_playing": None, "queue": []}, tracks=())

    panel = asyncio.run(music_client(spotify=False).update())

    assert panel.title == "Apple Music"
    assert panel.logo is None


@responses.activate
def test_spotify_labels_its_player_as_now_playing(client):
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)

    assert asyncio.run(client.update()).labels is SPOTIFY_LABELS


@responses.activate
def test_empty_top_lists_give_way_to_the_player(client):
    responses.add(responses.GET, QUEUE_URL, json=QUEUE)
    responses.add(responses.GET, TOP_TRACKS_URL, json={"items": []})
    responses.add(responses.GET, TOP_ARTISTS_URL, json={"items": []})
    asyncio.run(client.update())

    assert isinstance(asyncio.run(client.update()), SpotifyPanel)
