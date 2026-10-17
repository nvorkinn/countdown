"""The developer panel's turns in Spotify's area."""

import datetime as dt

import pytest

from countdown_api.models import Screen
from countdown_core.core.api_registry import ClientClasses
from countdown_core.rendering import layout_for, share_spotify_area
from countdown_core.rendering.layouts.classic import BaseLayout, SpotifyLayout
from countdown_core.spotify.spotify_client import SpotifyClient

SPOTIFY = object()
DEVELOPER = object()
PANELS = {ClientClasses.SPOTIFY.api_name: SPOTIFY, ClientClasses.DEVELOPER.api_name: DEVELOPER}
BOTH = frozenset({ClientClasses.SPOTIFY, ClientClasses.DEVELOPER})
LANDSCAPE = Screen(orientation="horizontal", width=800, height=480)


def at(minute: int) -> dt.datetime:
    return dt.datetime(2026, 10, 17, 9, minute)  # noqa: DTZ001 -- as the screen's clock is: local wall-clock time


def in_spotifys_area(panels, available, minute) -> object:
    return share_spotify_area(panels, available, at(minute))[ClientClasses.SPOTIFY.api_name]


def test_without_the_developer_panel_spotify_keeps_its_area():
    assert in_spotifys_area(PANELS, frozenset({ClientClasses.SPOTIFY}), 2) is SPOTIFY


@pytest.mark.parametrize("minute", [0, 1, 2, 3, 59])
def test_without_spotify_the_developer_panel_has_the_area_all_the_time(minute):
    assert in_spotifys_area(PANELS, frozenset({ClientClasses.DEVELOPER}), minute) is DEVELOPER


def test_with_both_the_developer_panel_has_one_minute_in_three():
    shown = [in_spotifys_area(PANELS, BOTH, minute) for minute in range(6)]

    assert shown == [SPOTIFY, SPOTIFY, DEVELOPER, SPOTIFY, SPOTIFY, DEVELOPER]


def test_the_developer_panel_stands_in_for_spotify_with_nothing_to_show():
    panels = PANELS | {ClientClasses.SPOTIFY.api_name: SpotifyClient.message_panel("Nothing playing on:")}

    assert in_spotifys_area(panels, BOTH, 0) is DEVELOPER


@pytest.mark.parametrize("minute", [0, 1, 2])
def test_without_a_developer_panel_to_show_spotify_keeps_its_area(minute):
    panels = {ClientClasses.SPOTIFY.api_name: SPOTIFY}

    assert in_spotifys_area(panels, BOTH, minute) is SPOTIFY


def test_the_panels_handed_in_are_left_as_they_were():
    share_spotify_area(PANELS, BOTH, at(2))

    assert PANELS[ClientClasses.SPOTIFY.api_name] is SPOTIFY


def test_the_developer_panel_alone_gets_the_layout_with_spotifys_area():
    """So the stops the departures fetch (stops_shown) match what's drawn."""
    developer_only = frozenset(ClientClasses) - {ClientClasses.SPOTIFY, ClientClasses.GLOWMARKT}

    assert type(layout_for(LANDSCAPE, developer_only)[0]) is SpotifyLayout
    assert type(layout_for(LANDSCAPE, developer_only - {ClientClasses.DEVELOPER})[0]) is BaseLayout
