"""The Spotify area reading the store: what auth-broker's music provider wrote (fakeredis).

fixtures/music_player.json and music_top.json are the stored contract: auth-broker's provider tests check that it
writes exactly these (its tests/fixtures holds the same files), and these tests check that the renderer reads each
part into the models it read the proxy routes' answers with when it asked the broker itself, so it draws the same
panels.
"""

import asyncio
import copy
import json
from pathlib import Path

import pytest
import responses
from test_utils import REGISTRATION

from countdown_core.spotify.models import Artist, Queue, TopResponse, Track
from countdown_core.spotify.spotify_client import (
    MusicSettings,
    NoStoredMusicError,
    SpotifyClient,
    music_client_class,
)
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_styles import APPLE_MUSIC_LABELS, SPOTIFY_LABELS
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_core.store import keys
from countdown_core.store.music import StoredPlayer, StoredTop

FIXTURES = Path(__file__).parent / "fixtures"
PLAYER = json.loads((FIXTURES / "music_player.json").read_text())
TOP = json.loads((FIXTURES / "music_top.json").read_text())
DEVICE_ID = REGISTRATION.device_id
NOTHING_PLAYING = {"currently_playing": None, "queue": []}
BOTH = MusicSettings(spotify=True, apple_music=True)


def put(store, player=PLAYER, top=TOP):
    """Store `player` and `top` for the device, as the provider does (None leaves a key out)."""
    if player is not None:
        store.set(keys.spotify(DEVICE_ID), json.dumps(player), ex=180)
    if top is not None:
        store.set(keys.spotify_top(DEVICE_ID), json.dumps(top), ex=7200)


def panels(client, count):
    return [asyncio.run(client.update()) for _ in range(count)]


# --- The contract -------------------------------------------------------------------


def test_the_fixtures_are_stored_values():
    player = StoredPlayer.model_validate(PLAYER)
    top = StoredTop.model_validate(TOP)

    assert player.spotify.currently_playing.name == "Espresso"
    assert player.apple_music.currently_playing.name == "Efecto"
    assert player.spotify.queue[1].album.images == []  # a local file: no cover
    assert [t.name for t in top.spotify["short_term"].tracks.items] == ["Birds of a Feather"]
    assert [a.name for a in top.apple_music["long_term"].artists.items] == ["Bad Bunny"]


@pytest.mark.parametrize("service", ["spotify", "apple_music"])
def test_each_stored_part_is_what_the_proxy_answered(service):
    """Read as the proxy routes' answers were (/queue, /top/tracks, /top/artists), so the panels are the same."""
    player, top = StoredPlayer.model_validate(PLAYER), StoredTop.model_validate(TOP)

    assert Queue.model_validate(PLAYER[service]) == getattr(player, service)
    assert TOP[service].keys() == {"long_term", "short_term"}
    for time_range, lists in TOP[service].items():
        stored = getattr(top, service)[time_range]
        assert TopResponse[Track].model_validate(lists["tracks"]) == stored.tracks
        assert TopResponse[Artist].model_validate(lists["artists"]) == stored.artists


def test_the_keys():
    assert keys.spotify("abc") == "v1:spotify:abc"
    assert keys.spotify_top("abc") == "v1:spotify-top:abc"


# --- Every page from the store -----------------------------------------------------


@pytest.mark.parametrize(
    "settings",
    [MusicSettings(spotify=True), MusicSettings(spotify=False, apple_music=True), BOTH],
    ids=["spotify", "apple-music", "both"],
)
@pytest.mark.parametrize("style", ["hero", "airy"])
def test_every_page_is_drawn_from_the_store_without_a_call(store, settings, style):
    settings = MusicSettings(settings.spotify, settings.apple_music, style)
    put(store)

    with responses.RequestsMock() as nothing:  # nothing registered: any request would fail
        found = panels(music_client_class(settings)(REGISTRATION, settings), 2 * len(pages_of(settings)) + 1)
        assert len(nothing.calls) == 0

    assert {type(p) for p in found} == {SpotifyPanel, SpotifyTopPanel}


def pages_of(settings):
    return SpotifyClient(REGISTRATION, settings).pages


@responses.activate
def test_both_on_rotates_through_both_services_top_lists(store):
    put(store)
    client = SpotifyClient(REGISTRATION, BOTH)

    found = panels(client, 8)

    tops = [(p.service, p.period) for p in found if isinstance(p, SpotifyTopPanel)]
    assert tops == [
        ("Spotify", "12 months"),
        ("Apple Music", "12 months"),
        ("Spotify", "4 weeks"),
        ("Apple Music", "4 weeks"),
    ]
    assert found[0].labels is SPOTIFY_LABELS
    assert len(responses.calls) == 0


# --- Nothing playing, and no data ---------------------------------------------------


@responses.activate
def test_nothing_playing_on_spotify_shows_apple_musics_last_played(store):
    put(store, {**PLAYER, "spotify": NOTHING_PLAYING})

    panel = asyncio.run(SpotifyClient(REGISTRATION, BOTH).update())

    assert isinstance(panel, SpotifyPanel)
    assert panel.labels is APPLE_MUSIC_LABELS
    assert panel.queue.currently_playing.name == "Efecto"


@responses.activate
def test_nothing_playing_anywhere_skips_to_the_top_lists(store):
    put(store, {**PLAYER, "spotify": NOTHING_PLAYING, "apple_music": NOTHING_PLAYING})

    panel = asyncio.run(SpotifyClient(REGISTRATION, BOTH).update())

    assert isinstance(panel, SpotifyTopPanel)
    assert panel.period == "12 months"


@responses.activate
def test_nothing_playing_and_no_top_lists_says_so(store):
    empty = {"tracks": {"items": []}, "artists": {"items": []}}
    put(
        store,
        {**PLAYER, "spotify": NOTHING_PLAYING, "apple_music": None},
        {**TOP, "spotify": {"long_term": empty, "short_term": empty}},
    )

    panel = asyncio.run(SpotifyClient(REGISTRATION, MusicSettings(spotify=True)).update())

    assert not isinstance(panel, (SpotifyPanel, SpotifyTopPanel))  # the "Nothing playing on:" message


@responses.activate
@pytest.mark.parametrize("missing", ["player", "top"])
def test_a_missing_key_raises_so_the_last_panel_stays(store, missing):
    put(store, player=None if missing == "player" else PLAYER, top=None if missing == "top" else TOP)
    client = SpotifyClient(REGISTRATION, MusicSettings(spotify=True))
    if missing == "top":
        asyncio.run(client.update())  # the player

    with pytest.raises(NoStoredMusicError):
        asyncio.run(client.update())
    assert issubclass(NoStoredMusicError, LookupError)
    assert len(responses.calls) == 0


@responses.activate
def test_a_service_the_provider_couldnt_fetch_raises(store):
    """Spotify's part is null (say its token was revoked): as when the proxy call fails, the last panel stays."""
    put(store, {**PLAYER, "spotify": None})

    with pytest.raises(NoStoredMusicError):
        asyncio.run(SpotifyClient(REGISTRATION, BOTH).update())


@responses.activate
def test_spotify_playing_needs_no_apple_music_part(store):
    """Spotify comes first, so a missing Apple Music part doesn't matter while it plays."""
    put(store, {**PLAYER, "apple_music": None})

    panel = asyncio.run(SpotifyClient(REGISTRATION, BOTH).update())

    assert panel.queue.currently_playing.name == "Espresso"


@responses.activate
def test_a_missing_time_range_raises(store):
    top = copy.deepcopy(TOP)
    del top["spotify"]["long_term"]
    put(store, top=top)
    client = SpotifyClient(REGISTRATION, MusicSettings(spotify=True))
    asyncio.run(client.update())

    with pytest.raises(NoStoredMusicError):
        asyncio.run(client.update())


@responses.activate
def test_reads_only_its_own_devices_keys(store):
    store.set(keys.spotify("someone-else"), json.dumps(PLAYER))

    with pytest.raises(NoStoredMusicError):
        asyncio.run(SpotifyClient(REGISTRATION, MusicSettings(spotify=True)).update())
