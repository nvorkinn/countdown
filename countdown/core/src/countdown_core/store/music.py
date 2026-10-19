"""What auth-broker's music provider stores for the Spotify area, and the renderer reads back.

The provider runs in auth-broker (broker/spotify_provider.py, see "Music in the store" in its README), which can't
import this, so the contract is the JSON itself: each service's part is in Spotify's own shapes (/me/player/queue,
/me/top/tracks, ...; Apple Music's made to look like them), read with the models in countdown_core.spotify.models.
Only these wrappers are the store's own. The store contract (countdown-api's store.yaml: StoredPlayer, StoredTop) pins
them, and its music_player.json and music_top.json (countdown_api.fixtures) are an example of each, which both
repositories' tests read, so neither side can drift alone.

A part is None when the service is switched off for the device, or the provider couldn't fetch it; nothing playing is
a Queue with no currently_playing, not None.

The playing track's cover isn't in the JSON: the provider stores the image itself, under keys.album_art(its URL), and
picks which of the album's sizes the same way as art_image. The contract's music_art.json is the keys it stores for
music_player.json (each holding music_art.jpg's bytes in the tests).
"""

from pydantic import BaseModel

from countdown_core.spotify.models import Album, Artist, Image, Queue, TopResponse, Track
from countdown_core.store.models import Stored

# The tallest a cover is drawn: the Spotify area is 120 px high in every layout (rendering/layouts). The renderer
# scales the stored one down to the style's size; a smaller one would stay small, as thumbnail() never scales up.
ART_SIZE = 120


def art_image(album: Album) -> Image | None:
    """Return which of the album's sizes of cover is the one in the store; None if it has none (a local file).

    The smallest at least ART_SIZE tall, else the tallest: Spotify's 300 px of its 640, 300 and 64, and the one 300 px
    image auth-broker asks Apple Music for. auth-broker's music provider (broker/album_art.py) picks the same way, so
    change both together.
    """
    if not album.images:
        return None
    tall_enough = [image for image in album.images if image.height >= ART_SIZE]
    if tall_enough:
        return min(tall_enough, key=lambda image: image.height)
    return max(album.images, key=lambda image: image.height)


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
