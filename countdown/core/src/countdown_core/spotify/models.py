"""Spotify's API responses, as far as the dashboard reads them."""

from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, HttpUrl

MillisecondTimedelta = Annotated[
    timedelta, BeforeValidator(lambda v: timedelta(milliseconds=v) if isinstance(v, (int, float)) else v)
]


class Image(BaseModel):
    """One size of an album's cover art."""

    width: int
    height: int
    url: HttpUrl


class Album(BaseModel):
    """A track's album: its name and its cover in several sizes (none for a local file)."""

    images: list[Image]
    name: str


class Artist(BaseModel):
    """An artist, on a track or in the top artists."""

    name: str


class Track(BaseModel):
    """A track, playing, queued or in the top tracks."""

    album: Album
    artists: list[Artist]
    name: str
    duration_ms: MillisecondTimedelta


class Queue(BaseModel):
    """What's playing and what's up after it, from Spotify's /me/player/queue."""

    currently_playing: Track | None
    queue: list[Track]


class TopResponse[T: (Track, Artist)](BaseModel):
    """The listener's top tracks or artists, from Spotify's /me/top/{type}."""

    items: list[T]
