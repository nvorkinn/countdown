"""Spotify's API responses, as far as the dashboard reads them."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, BeforeValidator, Field

MillisecondTimedelta = Annotated[
    timedelta, BeforeValidator(lambda v: timedelta(milliseconds=v) if isinstance(v, (int, float)) else v)
]


class Image(BaseModel):
    """One size of an album's cover art."""

    width: int
    height: int
    # Exactly as the service wrote it, not normalised as a URL: the cover's key in the store is made from this string
    # (store.keys.album_art), as countdown-server's music provider made it. The renderer never fetches it.
    url: str


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
    # Spotify's track id: what a Playback names the track its position belongs to. Missing for a local file, and for
    # Apple Music's tracks, which have none.
    id: str | None = None


class Queue(BaseModel):
    """What's playing and what's up after it, from Spotify's /me/player/queue."""

    currently_playing: Track | None
    queue: list[Track]


# How old a position can be and still count as the player playing: the provider fetches one every 20 s while a track
# plays, so this is a few missed fetches. After it the player may well have stopped, and nothing says so.
PLAYBACK_FRESH = timedelta(seconds=90)


class Playback(BaseModel):
    """Where Spotify's player was in a track, from its /me/player/currently-playing as of `fetched_at`.

    The music provider fetches this far more often than the queue while something plays, so it has a time of its own,
    which may be newer than the queue's (`StoredPlayer.fetched_at`). Apple Music has no position.
    """

    fetched_at: AwareDatetime
    is_playing: bool
    progress_ms: Annotated[int, Field(ge=0)]
    # The track the position belongs to; None for one without an id (a local file).
    track_id: str | None = None

    def is_of(self, track: Track) -> bool:
        """Whether this is the position of `track`: it has an id, and it is the one `track_id` names."""
        return track.id is not None and self.track_id == track.id

    def is_playing_now(self, track: Track, now: datetime) -> bool:
        """Whether `track` is playing at `now`: this is its position, it was playing, and it was read recently."""
        return self.is_of(track) and self.is_playing and timedelta(0) <= now - self.fetched_at <= PLAYBACK_FRESH

    def is_paused(self, track: Track) -> bool:
        """Whether `track` is paused: this is its position, and the player was not playing."""
        return self.is_of(track) and not self.is_playing

    def elapsed(self, track: Track, now: datetime) -> timedelta | None:
        """Return how far into `track` the player is at `now`; None if this position isn't known to be the track's.

        While playing, the position the provider read plus the time since it read it, so the bar moves between
        fetches; paused, just the position it read. Never before the start or past the track's end, as the clock may
        run behind the provider's, and the track may have ended since. None when `track_id` isn't the track's id (the
        queue is of another track than the position, as just after a skip), and for a track without an id: no bar is
        better than a wrong one.
        """
        if not self.is_of(track):
            return None
        position = timedelta(milliseconds=self.progress_ms)
        if self.is_playing:
            position += now - self.fetched_at
        return min(max(position, timedelta(0)), track.duration_ms)


class TopResponse[T: (Track, Artist)](BaseModel):
    """The listener's top tracks or artists, from Spotify's /me/top/{type}."""

    items: list[T]
