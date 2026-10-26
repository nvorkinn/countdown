"""The Spotify area reading the store: what countdown-server's music provider wrote (fakeredis).

The store contract's music_player.json and music_top.json (countdown_api.fixtures) are the stored values: the broker's
provider tests check that it writes exactly these, and these tests check that the renderer reads each
part into the models of Spotify's own answers (countdown_core.spotify.models), so both services draw the same
panels. The contract's music_art.json is the covers it stores for music_player.json, by key, each the raw bytes of the
image (music_art.jpg).
"""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
import responses
from redis.exceptions import ConnectionError as RedisConnectionError
from test_utils import TEST_DEVICE_ID

from countdown_api import fixtures
from countdown_core.spotify.models import Artist, Queue, TopResponse, Track
from countdown_core.store import keys, reader
from countdown_core.store.music import StoredPlayer, StoredTop, art_image
from countdown_renderer import rotation
from countdown_renderer.spotify import spotify_styles
from countdown_renderer.spotify.spotify_client import (
    TOP_EVERY_S,
    TOP_WHEN_IDLE_EVERY_S,
    TOP_WHILE_PLAYING_S,
    MusicSettings,
    NoStoredMusicError,
    SpotifyClient,
    music_client_class,
)
from countdown_renderer.spotify.spotify_panel import SpotifyPanel
from countdown_renderer.spotify.spotify_styles import APPLE_MUSIC_LABELS, SPOTIFY_LABELS
from countdown_renderer.spotify.spotify_top_panel import SpotifyTopPanel
from countdown_renderer.utils.utils import fitted_font

PLAYER = fixtures.load("music_player.json")
TOP = fixtures.load("music_top.json")
COVERS = fixtures.load("music_art.json")
ART = fixtures.read_bytes("music_art.jpg")
DEVICE_ID = TEST_DEVICE_ID
NOTHING_PLAYING = {"currently_playing": None, "queue": []}
BOTH = MusicSettings(spotify=True, apple_music=True)


def put(store, player=PLAYER, top=TOP, *, fresh=True):
    """Store `player` and `top` for the device, as the provider does (None leaves a key out).

    If `fresh`, Spotify's position (if it has one) is as read now (rotation.now), so a playing track is playing.
    """
    if player is not None and fresh and player.get("spotify_playback"):
        player = {**player, "spotify_playback": player["spotify_playback"] | {"fetched_at": rotation.now().isoformat()}}
    if player is not None:
        store.set(keys.spotify(DEVICE_ID), json.dumps(player), ex=180)
    if top is not None:
        store.set(keys.spotify_top(DEVICE_ID), json.dumps(top), ex=7200)


def put_art(store, *services):
    """Store each service's cover (music_art.json's key) as the provider does: the image's own bytes."""
    for service in services:
        store.set(COVERS[service]["key"], ART, ex=300)


def visits(client, count, rotation_clock):
    """Update at the start of `count` visits in a row while nothing plays (TOP_WHEN_IDLE_EVERY_S apart)."""
    found = []
    for visit in range(count):
        rotation_clock.at_minute(visit * TOP_WHEN_IDLE_EVERY_S // 60)
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


def test_the_fixture_has_where_spotify_was_in_the_playing_track():
    playback = StoredPlayer.model_validate(PLAYER).spotify_playback

    assert (playback.is_playing, playback.progress_ms, playback.track_id) == (True, 61234, "tab01")
    assert playback.track_id == PLAYER["spotify"]["currently_playing"]["id"]


def test_a_key_without_a_position_still_reads():
    """Keys written before the field, and ones with nothing playing, have none."""
    without = {k: v for k, v in PLAYER.items() if k != "spotify_playback"}

    assert StoredPlayer.model_validate(without).spotify_playback is None
    assert StoredPlayer.model_validate({**PLAYER, "spotify_playback": None}).spotify_playback is None


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
        client = music_client_class(settings)(TEST_DEVICE_ID, settings)
        found = [asyncio.run(client.update())]  # playing, if Spotify's on
        put(store, fresh=False)  # then nothing is: every page visits in turn
        found += visits(client, len(pages_of(settings)), rotation_clock)
        assert len(nothing.calls) == 0

    assert {type(p) for p in found} == {SpotifyPanel, SpotifyTopPanel}


def pages_of(settings):
    return SpotifyClient(TEST_DEVICE_ID, settings).pages


@responses.activate
def test_both_on_rotates_through_both_services_top_lists(store, rotation_clock):
    put(store, fresh=False)  # nothing playing
    client = SpotifyClient(TEST_DEVICE_ID, BOTH)

    found = visits(client, 8, rotation_clock)

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
        panel = asyncio.run(music_client_class(settings)(TEST_DEVICE_ID, settings).update())
        image = panel.render(536, 120)
        assert len(nothing.calls) == 0

    assert panel.art == ART
    assert _cover_strip_alpha(image) == (255, 255)  # drawn


def test_each_service_shows_its_own_cover(store):
    """Covers are keyed by image, so Apple Music's last played doesn't get Spotify's."""
    put(store, {**PLAYER, "spotify": NOTHING_PLAYING})
    store.set(COVERS["spotify"]["key"], b"spotify's", ex=300)
    store.set(COVERS["apple_music"]["key"], b"apple music's", ex=300)

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, BOTH).update())

    assert panel.art == b"apple music's"


def test_two_screens_playing_the_same_album_read_the_same_cover(store):
    other = "another-device"
    put(store)
    store.set(keys.spotify("another-device"), store.get(keys.spotify(DEVICE_ID)), ex=180)
    put_art(store, "spotify")
    settings = MusicSettings(spotify=True)

    mine = asyncio.run(SpotifyClient(TEST_DEVICE_ID, settings).update())
    theirs = asyncio.run(SpotifyClient(other, settings).update())

    assert mine.art == theirs.art == ART
    assert store.keys("v1:art:*") == [COVERS["spotify"]["key"]]


def test_a_missing_cover_draws_the_player_without_one(store, symbols):
    """The provider couldn't fetch it, or Redis evicted it: the page is still drawn, just without a cover."""
    put(store)

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True)).update())
    image = panel.render(536, 120)

    assert isinstance(panel, SpotifyPanel)
    assert panel.art is None
    assert _cover_strip_alpha(image) == (0, 0)  # the page's transparent background: no cover


def _cover_strip_alpha(image):
    """Return the alpha range of a strip of where the hero style puts the cover, with nothing else drawn there.

    Between its header's rule and the title, so there is no text in it when there is no cover for it to sit beside,
    whether or not the cover is drawn smaller for a progress bar.
    """
    return image.crop((0, 29, 100, 33)).getextrema()[3]


def test_a_cover_redis_cant_hand_over_draws_the_player_without_one(store, monkeypatch):
    put(store)
    client = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))

    def down(key):
        raise RedisConnectionError(key)

    monkeypatch.setattr(client.art_store, "get", down)

    panel = asyncio.run(client.update())

    assert isinstance(panel, SpotifyPanel)
    assert panel.art is None


def test_a_track_with_no_cover_reads_none(store, monkeypatch):
    """A local file: nothing to read."""
    put(store, {**PLAYER, "spotify": {"currently_playing": PLAYER["spotify"]["queue"][1], "queue": []}})
    client = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))
    monkeypatch.setattr(client.art_store, "get", lambda key: pytest.fail(f"read {key}"))

    assert client.update_player().art is None


# --- Nothing playing, and no data ---------------------------------------------------


@responses.activate
def test_nothing_playing_on_spotify_shows_apple_musics_last_played(store):
    put(store, {**PLAYER, "spotify": NOTHING_PLAYING})

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, BOTH).update())

    assert isinstance(panel, SpotifyPanel)
    assert panel.labels is APPLE_MUSIC_LABELS
    assert panel.queue.currently_playing.name == "Efecto"


@responses.activate
def test_nothing_playing_anywhere_skips_to_the_top_lists(store):
    put(store, {**PLAYER, "spotify": NOTHING_PLAYING, "apple_music": NOTHING_PLAYING})

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, BOTH).update())

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

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True)).update())

    assert not isinstance(panel, (SpotifyPanel, SpotifyTopPanel))  # the "Nothing playing on:" message


@responses.activate
def test_a_missing_player_key_is_nothing_playing(store, rotation_clock):
    """The key has expired (the music provider is down): nothing older than its TTL is drawn, and nothing plays."""
    put(store, player=None)
    client = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))

    with pytest.raises(NoStoredMusicError):
        client.update_player()
    assert isinstance(asyncio.run(client.update()), SpotifyTopPanel)  # a visit's minute
    rotation_clock.advance()
    assert client.is_idle()


@responses.activate
def test_a_missing_key_says_the_music_is_not_available(store, rotation_clock):
    """The top lists' key has expired while they visit: nothing older than its TTL is drawn."""
    put(store, top=None, fresh=False)
    client = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))

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
    client = SpotifyClient(TEST_DEVICE_ID, BOTH)

    with pytest.raises(NoStoredMusicError):
        client._update()
    assert asyncio.run(client.update()).message == "Not available"


@responses.activate
def test_spotify_playing_needs_no_apple_music_part(store):
    """Spotify comes first, so a missing Apple Music part doesn't matter while it plays."""
    put(store, {**PLAYER, "apple_music": None})

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, BOTH).update())

    assert panel.queue.currently_playing.name == "Espresso"


@responses.activate
def test_a_missing_time_range_raises(store, rotation_clock):
    top = copy.deepcopy(TOP)
    del top["spotify"]["long_term"]
    put(store, top=top, fresh=False)  # nothing playing: the 12 months' top lists visit
    client = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))

    with pytest.raises(NoStoredMusicError):
        client._update()
    assert asyncio.run(client.update()).message == "Not available"


@responses.activate
def test_reads_only_its_own_devices_keys(store):
    store.set(keys.spotify("someone-else"), json.dumps(PLAYER))

    with pytest.raises(NoStoredMusicError):
        SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))._update()


# --- The progress bar's position --------------------------------------------------------------


@responses.activate
def test_spotifys_panel_gets_the_stored_position(store):
    put(store)

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True)).update())

    assert panel.playback.track_id == "tab01"
    assert panel.playback.progress_ms == 61234


@responses.activate
def test_a_key_without_a_position_gives_a_panel_without_one(store):
    put(store, {k: v for k, v in PLAYER.items() if k != "spotify_playback"})

    panel = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True)).update_player()

    assert panel.playback is None


@responses.activate
def test_apple_music_has_no_position_even_if_the_key_has_spotifys(store, symbols):
    """Apple Music's last played track is no position of Spotify's, and its panel draws exactly what it did."""
    put(store)
    apple = MusicSettings(spotify=False, apple_music=True)

    panel = asyncio.run(SpotifyClient(TEST_DEVICE_ID, apple).update())

    assert panel.labels is APPLE_MUSIC_LABELS
    assert panel.playback is None
    assert (
        panel.render(536, 120).tobytes()
        == SpotifyPanel(panel.queue, panel.style, panel.labels).render(536, 120).tobytes()
    )


# --- While a track plays, the top lists are only a short visit ---------------------------

# A time on a boundary of TOP_EVERY_S and an even number of them from the epoch, so the first visit is the first list's.
BOUNDARY = datetime.fromtimestamp(TOP_EVERY_S * 7_000_000, UTC)


def player_read_at(at, *, is_playing=True):
    """Return the fixture's player with Spotify's position read at `at`, as the music provider's burst does."""
    player = copy.deepcopy(PLAYER)
    player["spotify_playback"] |= {"fetched_at": at.isoformat(), "is_playing": is_playing}
    return player


def shown_every_20s(store, rotation_clock, seconds, *, is_playing=True, top=TOP):
    """Return what the area shows every 20 s for `seconds` from BOUNDARY, the position read at each one."""
    client = SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True))
    found = []
    for offset in range(0, seconds, 20):
        rotation_clock.at = BOUNDARY + timedelta(seconds=offset)
        put(store, player_read_at(rotation_clock.at, is_playing=is_playing), top, fresh=False)
        found.append(asyncio.run(client.update()))
    return found


def test_while_playing_a_top_list_is_shown_for_20_seconds_every_4_minutes(store, rotation_clock):
    found = shown_every_20s(store, rotation_clock, 2 * TOP_EVERY_S)

    visits = [i for i, panel in enumerate(found) if isinstance(panel, SpotifyTopPanel)]
    # The last 20 s of each 4 minutes, and the player the rest of the time.
    assert TOP_WHILE_PLAYING_S == 20
    assert visits == [TOP_EVERY_S // 20 - 1, 2 * TOP_EVERY_S // 20 - 1]
    assert all(isinstance(panel, SpotifyPanel) for i, panel in enumerate(found) if i not in visits)


def test_each_visit_shows_the_next_top_list(store, rotation_clock):
    found = shown_every_20s(store, rotation_clock, 3 * TOP_EVERY_S)

    periods = [panel.period for panel in found if isinstance(panel, SpotifyTopPanel)]
    assert periods == ["12 months", "4 weeks", "12 months"]


# Ten minutes into a TOP_WHEN_IDLE_EVERY_S: no page visiting while nothing plays.
BETWEEN_VISITS = datetime.fromtimestamp(TOP_WHEN_IDLE_EVERY_S * 1_000_000 + 600, UTC)


def is_idle_with(store, rotation_clock, player) -> bool:
    rotation_clock.at = BETWEEN_VISITS
    put(store, player, fresh=False)
    return SpotifyClient(TEST_DEVICE_ID, MusicSettings(spotify=True)).is_idle()


def test_a_track_playing_keeps_the_area(store, rotation_clock):
    assert not is_idle_with(store, rotation_clock, player_read_at(BETWEEN_VISITS))


def test_a_paused_player_is_nothing_playing(store, rotation_clock):
    """Paused, the area folds away: the stops and notices get its room."""
    assert is_idle_with(store, rotation_clock, player_read_at(BETWEEN_VISITS, is_playing=False))


def test_a_position_read_long_ago_is_not_playing(store, rotation_clock):
    """The provider stopped fetching (or Spotify did): nothing says it is still playing."""
    assert is_idle_with(store, rotation_clock, player_read_at(BETWEEN_VISITS - timedelta(minutes=5)))


def test_a_visit_with_nothing_to_show_stays_on_the_player(store, rotation_clock):
    empty = copy.deepcopy(TOP)
    for lists in empty["spotify"].values():
        lists["tracks"]["items"] = []
        lists["artists"]["items"] = []

    found = shown_every_20s(store, rotation_clock, TOP_EVERY_S, top=empty)

    assert all(isinstance(panel, SpotifyPanel) for panel in found)


def test_a_player_without_a_position_is_nothing_playing(store, rotation_clock):
    """The key the provider wrote before it read positions: no playback, so nothing says it is playing."""
    player = {key: value for key, value in PLAYER.items() if key != "spotify_playback"}

    assert is_idle_with(store, rotation_clock, player)
