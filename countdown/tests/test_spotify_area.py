"""Where the developer panel goes: a strip above Spotify's song, or Spotify's whole area."""

import datetime as dt

from countdown_api.models import Screen
from countdown_core.core.api_registry import ClientClasses
from countdown_core.developer.developer_panel import DeveloperPanel
from countdown_core.developer.developer_styles import COMPACT_HEIGHT
from countdown_core.developer.models import DeveloperSummary, GitHubSummary, Release
from countdown_core.developer.release_styles import COMPACT_HEIGHT as RELEASE_STRIP
from countdown_core.rendering import layout_for, place_developer_panel
from countdown_core.rendering.layouts.classic import SPOTIFY_HEIGHT, BaseLayout, SpotifyLayout
from countdown_core.spotify.models import Queue
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.spotify.spotify_panel import SpotifyPanel
from countdown_core.spotify.spotify_top_panel import SpotifyTopPanel

SPOTIFY = SpotifyPanel(Queue(currently_playing=None, queue=[]))
GITHUB = GitHubSummary(login="nvorkinn", repos=1, open_prs=0, review_requests=0, failing=[])
DEVELOPER = DeveloperPanel(DeveloperSummary(github=GITHUB, plans=[]))
PANELS = {ClientClasses.SPOTIFY.api_name: SPOTIFY, ClientClasses.DEVELOPER.api_name: DEVELOPER}
BOTH = frozenset({ClientClasses.SPOTIFY, ClientClasses.DEVELOPER})
LANDSCAPE = Screen(orientation="horizontal", width=800, height=480)
SPOTIFYS = ClientClasses.SPOTIFY.api_name
DEVELOPERS = ClientClasses.DEVELOPER.api_name


def test_without_the_developer_panel_spotify_keeps_its_area():
    placed = place_developer_panel(PANELS, frozenset({ClientClasses.SPOTIFY}))

    assert placed == {SPOTIFYS: SPOTIFY}


def test_without_spotify_the_developer_panel_has_the_whole_area():
    placed = place_developer_panel(PANELS, frozenset({ClientClasses.DEVELOPER}))

    assert placed == {SPOTIFYS: DEVELOPER}


def test_with_a_song_showing_the_developer_panel_is_a_strip_above_it():
    placed = place_developer_panel(PANELS, BOTH)

    assert placed[SPOTIFYS] is SPOTIFY
    assert placed[DEVELOPERS].compact
    assert placed[DEVELOPERS].fitted_height(SPOTIFY_HEIGHT) == COMPACT_HEIGHT


def test_the_developer_panel_has_the_whole_area_while_spotify_has_nothing_to_show():
    panels = PANELS | {SPOTIFYS: SpotifyClient.message_panel("Nothing playing on:")}

    assert place_developer_panel(panels, BOTH) == {SPOTIFYS: DEVELOPER}


def test_a_developer_message_is_left_out_above_spotify():
    panels = PANELS | {DEVELOPERS: SpotifyClient.message_panel("Not available")}

    assert place_developer_panel(panels, BOTH) == {SPOTIFYS: SPOTIFY}


def test_on_a_top_list_the_developer_panel_has_the_whole_area():
    panels = PANELS | {SPOTIFYS: SpotifyTopPanel("4 weeks", [], [])}

    assert place_developer_panel(panels, BOTH) == {SPOTIFYS: DEVELOPER}


def test_without_a_developer_panel_to_show_spotify_keeps_its_area():
    assert place_developer_panel({SPOTIFYS: SPOTIFY}, BOTH) == {SPOTIFYS: SPOTIFY}


def test_the_panels_handed_in_are_left_as_they_were():
    place_developer_panel(PANELS, BOTH)

    assert PANELS == {SPOTIFYS: SPOTIFY, DEVELOPERS: DEVELOPER}
    assert not DEVELOPER.compact


def test_the_developer_panel_alone_gets_the_layout_with_spotifys_area():
    """So the stops the departures fetch (stops_shown) match what's drawn."""
    developer_only = frozenset(ClientClasses) - {ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT}

    assert type(layout_for(LANDSCAPE, developer_only)[0]) is SpotifyLayout
    assert type(layout_for(LANDSCAPE, developer_only - {ClientClasses.DEVELOPER})[0]) is BaseLayout


def test_a_release_above_spotify_is_a_taller_strip():
    """Its jobs in a row, under its name."""
    release = Release(
        repo="auth-broker",
        title="v1.27.0",
        state="running",
        started=dt.datetime(2026, 10, 17, 9, 0, tzinfo=dt.UTC),
        jobs=[],
    )
    developer = DeveloperPanel(DeveloperSummary(github=GITHUB.model_copy(update={"releases": [release]}), plans=[]))

    placed = place_developer_panel(PANELS | {DEVELOPERS: developer}, BOTH)

    assert placed[DEVELOPERS].fitted_height(SPOTIFY_HEIGHT) == RELEASE_STRIP
    # A run that hasn't started its jobs yet: just its name, no row of boxes.
    assert placed[DEVELOPERS].render(536, RELEASE_STRIP).size == (536, RELEASE_STRIP)
