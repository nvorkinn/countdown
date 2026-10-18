"""The Spotify area reading the store: what auth-broker's music provider wrote (fakeredis).

fixtures/music_player.json and music_top.json are the stored contract: auth-broker's provider tests check that it
writes exactly these (its tests/fixtures holds the same files), and these tests check that the renderer reads each
part into the models of Spotify's own answers (countdown_core.spotify.models), so both services draw the same
panels. fixtures/music_art.json is the covers it stores for music_player.json, by key, each the raw bytes of the
image (music_art.jpg in both repositories' tests).
"""

import asyncio
import copy
import dataclasses
import json
from pathlib import Path

import pytest
import responses
from redis.exceptions import ConnectionError as RedisConnectionError
from test_utils import REGISTRATION

from countdown_core.spotify import spotify_styles
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
from countdown_core.store import keys, reader
from countdown_core.store.music import StoredPlayer, StoredTop, art_image
from countdown_core.utils.utils import fitted_font

FIXTURES = Path(__file__).parent / "fixtures"
PLAYER = json.loads((FIXTURES / "music_player.json").read_text())
TOP = json.loads((FIXTURES / "music_top.json").read_text())
COVERS = json.loads((FIXTURES / "music_art.json").read_text())
ART = (FIXTURES / "music_art.jpg").read_bytes()
DEVICE_ID = REGISTRATION.device_id
NOTHING_PLAYING = {"currently_playing": None, "queue": []}
BOTH = MusicSettings(spotify=True, apple_music=True)


def put(store, player=PLAYER, top=TOP):
    """Store `player` and `top` for the device, as the provider does (None leaves a key out)."""
    if player is not None:
        store.set(keys.spotify(DEVICE_ID), json.dumps(player), ex=180)
    if top is not None:
        store.set(keys.spotify_top(DEVICE_ID), json.dumps(top), ex=7200)


def put_art(store, *services):
    """Store each service's cover (music_art.json's key) as the provider does: the image's own bytes."""
    for service in services:
        store.set(COVERS[service]["key"], ART, ex=300)


def panels(client, count, rotation_clock):
    """Update `count` times, a minute apart, as the registry polls: a page a minute (core.rotation)."""
    found = []
    for minute in range(count):
        rotation_clock.at_minute(minute)
        found.append(asyncio.run(client.update()))
    return found


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
def test_each_stored_part_is_in_spotifys_shapes(service):
    """Read as Spotify's own answers (/me/player/queue, /me/top/tracks, /me/top/artists), so the panels are the same."""
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


@pytest.mark.parametrize("service", ["spotify", "apple_music"])
def test_the_cover_is_read_by_the_key_the_provider_stores_it_under(service):
    """The provider picks the same size (Spotify's 300 px, Apple Music's one) and makes the key from its URL."""
    playing = getattr(StoredPlayer.model_validate(PLAYER), service).currently_playing

    image = art_image(playing.album)

    assert image.url == COVERS[service]["url"]
    assert keys.album_art(image.url) == COVERS[service]["key"]


def test_a_cover_key_is_the_sha1_of_the_url_exactly_as_given():
    assert keys.album_art("https://i.scdn.co/image/ab01300") == "v1:art:f0c75b31e316838c7115ebf3205ef753eb4a0353"
    # Not normalised: a URL is only ever compared with itself, as the service wrote it.
    assert keys.album_art("https://example.com") != keys.album_art("https://example.com/")


# --- Every page from the store -----------------------------------------------------


@pytest.mark.parametrize(
    "settings",
    [MusicSettings(spotify=True), MusicSettings(spotify=False, apple_music=True), BOTH],
    ids=["spotify", "apple-music", "both"],
)
@pytest.mark.parametrize("style", ["hero", "airy"])
def test_every_page_is_drawn_from_the_store_without_a_call(store, settings, style, rotation_clock):
    settings = MusicSettings(settings.spotify, settings.apple_music, style)
    put(store)

    with responses.RequestsMock() as nothing:  # nothing registered: any request would fail
        client = music_client_class(settings)(REGISTRATION, settings)
        found = panels(client, 2 * len(pages_of(settings)) + 1, rotation_clock)
        assert len(nothing.calls) == 0

    assert {type(p) for p in found} == {SpotifyPanel, SpotifyTopPanel}


def pages_of(settings):
    return SpotifyClient(REGISTRATION, settings).pages


@responses.activate
def test_both_on_rotates_through_both_services_top_lists(store, rotation_clock):
    put(store)
    client = SpotifyClient(REGISTRATION, BOTH)

    found = panels(client, 8, rotation_clock)

    tops = [(p.service, p.period) for p in found if isinstance(p, SpotifyTopPanel)]
    assert tops == [
        ("Spotify", "12 months"),
        ("Apple Music", "12 months"),
        ("Spotify", "4 weeks"),
        ("Apple Music", "4 weeks"),
    ]
    assert found[0].labels is SPOTIFY_LABELS
    assert len(responses.calls) == 0


# --- The cover ---------------------------------------------------------------------


@pytest.fixture
def symbols(monkeypatch):
    """Stand in for Material Symbols with a font the package has, so drawing a page needs no download."""
    monkeypatch.setattr(spotify_styles, "google_symbols", lambda: fitted_font("Ubuntu-Regular.ttf", 15))


@pytest.mark.parametrize(
    ("settings", "player"),
    [
        (MusicSettings(spotify=True), PLAYER),
        (MusicSettings(spotify=False, apple_music=True), PLAYER),
        (BOTH, {**PLAYER, "spotify": NOTHING_PLAYING}),
    ],
    ids=["spotify", "apple-music", "apple-music-while-spotify-is-silent"],
)
def test_the_playing_tracks_cover_comes_from_the_store_without_a_call(store, symbols, settings, player):
    put(store, player)
    put_art(store, "spotify", "apple_music")

    with responses.RequestsMock() as nothing:  # nothing registered: any request would fail
        panel = asyncio.run(music_client_class(settings)(REGISTRATION, settings).update())
        image = panel.render(536, 120)
        assert len(nothing.calls) == 0

    assert panel.art == ART
    assert _cover_strip_alpha(image) == (255, 255)  # drawn


def test_each_service_shows_its_own_cover(store):
    """Covers are keyed by image, so Apple Music's last played doesn't get Spotify's."""
    put(store, {**PLAYER, "spotify": NOTHING_PLAYING})
    store.set(COVERS["spotify"]["key"], b"spotify's", ex=300)
    store.set(COVERS["apple_music"]["key"], b"apple music's", ex=300)

    panel = asyncio.run(SpotifyClient(REGISTRATION, BOTH).update())

    assert panel.art == b"apple music's"


def test_two_screens_playing_the_same_album_read_the_same_cover(store):
    other = dataclasses.replace(REGISTRATION, device_id="another-device")
    put(store)
    store.set(keys.spotify("another-device"), json.dumps(PLAYER), ex=180)
    put_art(store, "spotify")
    settings = MusicSettings(spotify=True)

    mine = asyncio.run(SpotifyClient(REGISTRATION, settings).update())
    theirs = asyncio.run(SpotifyClient(other, settings).update())

    assert mine.art == theirs.art == ART
    assert store.keys("v1:art:*") == [COVERS["spotify"]["key"]]


def test_a_missing_cover_draws_the_player_without_one(store, symbols):
    """The provider couldn't fetch it, or Redis evicted it: the page is still drawn, just without a cover."""
    put(store)

    panel = asyncio.run(SpotifyClient(REGISTRATION, MusicSettings(spotify=True)).update())
    image = panel.render(536, 120)

    assert isinstance(panel, SpotifyPanel)
    assert panel.art is None
    assert _cover_strip_alpha(image) == (0, 0)  # the page's transparent background: no cover


def _cover_strip_alpha(image):
    """Return the alpha range of the bottom of where the hero style puts the cover, under any text drawn beside it."""
    return image.crop((0, 113, 120, 120)).getextrema()[3]


def test_a_cover_redis_cant_hand_over_draws_the_player_without_one(store, monkeypatch):
    put(store)
    client = SpotifyClient(REGISTRATION, MusicSettings(spotify=True))

    def down(key):
        raise RedisConnectionError(key)

    monkeypatch.setattr(client.art_store, "get", down)

    panel = asyncio.run(client.update())

    assert isinstance(panel, SpotifyPanel)
    assert panel.art is None


def test_a_track_with_no_cover_reads_none(store, monkeypatch):
    """A local file: nothing to read."""
    put(store, {**PLAYER, "spotify": {"currently_playing": PLAYER["spotify"]["queue"][1], "queue": []}})
    client = SpotifyClient(REGISTRATION, MusicSettings(spotify=True))
    monkeypatch.setattr(client.art_store, "get", lambda key: pytest.fail(f"read {key}"))

    assert asyncio.run(client.update()).art is None


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
def test_a_missing_key_says_the_music_is_not_available(store, missing, rotation_clock):
    """The key has expired (the music provider is down): nothing older than its TTL is drawn."""
    put(store, player=None if missing == "player" else PLAYER, top=None if missing == "top" else TOP)
    client = SpotifyClient(REGISTRATION, MusicSettings(spotify=True))
    if missing == "top":
        asyncio.run(client.update())  # the player
        rotation_clock.advance()  # the top lists' minute

    with pytest.raises(NoStoredMusicError):
        client._update()
    panel = asyncio.run(client.update())

    assert (panel.title, panel.message, panel.logo) == ("Spotify", "Not available", "spotify_logo.png")
    assert issubclass(NoStoredMusicError, reader.NoStoredDataError)
    assert len(responses.calls) == 0


@responses.activate
def test_a_service_the_provider_couldnt_fetch_is_not_available(store):
    """Spotify's part is null (say its token was revoked): the panel says so, rather than show what played before."""
    put(store, {**PLAYER, "spotify": None})
    client = SpotifyClient(REGISTRATION, BOTH)

    with pytest.raises(NoStoredMusicError):
        client._update()
    assert asyncio.run(client.update()).message == "Not available"


@responses.activate
def test_spotify_playing_needs_no_apple_music_part(store):
    """Spotify comes first, so a missing Apple Music part doesn't matter while it plays."""
    put(store, {**PLAYER, "apple_music": None})

    panel = asyncio.run(SpotifyClient(REGISTRATION, BOTH).update())

    assert panel.queue.currently_playing.name == "Espresso"


@responses.activate
def test_a_missing_time_range_raises(store, rotation_clock):
    top = copy.deepcopy(TOP)
    del top["spotify"]["long_term"]
    put(store, top=top)
    client = SpotifyClient(REGISTRATION, MusicSettings(spotify=True))
    asyncio.run(client.update())
    rotation_clock.advance()  # the 12 months' top lists' minute

    with pytest.raises(NoStoredMusicError):
        client._update()
    assert asyncio.run(client.update()).message == "Not available"


@responses.activate
def test_reads_only_its_own_devices_keys(store):
    store.set(keys.spotify("someone-else"), json.dumps(PLAYER))

    with pytest.raises(NoStoredMusicError):
        SpotifyClient(REGISTRATION, MusicSettings(spotify=True))._update()
