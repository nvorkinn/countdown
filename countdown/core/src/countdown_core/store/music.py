"""What auth-broker's music provider stores for the Spotify area, and the renderer reads back.

The provider runs in auth-broker (broker/spotify_provider.py, see "Music in the store" in its README), which can't
import this, so the contract is the JSON itself: each service's part is exactly what the broker's proxy route answers
a renderer (/api/spotify/queue, /api/apple-music/top/tracks, ...), read with the models the renderer already
has (countdown_core.spotify.models). Only these wrappers are new. tests/fixtures/music_player.json and music_top.json
are an example of each, copied into both repositories' tests so neither side can drift alone.

A part is None when the service is switched off for the device, or the provider couldn't fetch it; nothing playing is
a Queue with no currently_playing, not None.
"""

from pydantic import BaseModel

from countdown_core.spotify.models import Artist, Queue, TopResponse, Track
from countdown_core.store.models import Stored


class StoredPlayer(Stored):
    """What each service is playing, and what's up next: the value of `v1:spotify:<device id>` (TTL ~3 min).

    Spotify's part is its /me/player/queue; Apple Music's, the last played track and the ones before it.
    """

    spotify: Queue | None = None
    apple_music: Queue | None = None


class TopLists(BaseModel):
    """A service's top five tracks and artists over one time range, as its /top/tracks and /top/artists return them."""

    tracks: TopResponse[Track]
    artists: TopResponse[Artist]


class StoredTop(Stored):
    """Each service's top lists by Spotify's time range: the value of `v1:spotify-top:<device id>` (TTL ~2 h).

    Keyed "long_term" and "short_term", the ranges the Spotify area's top pages show.
    """

    spotify: dict[str, TopLists] | None = None
    apple_music: dict[str, TopLists] | None = None
