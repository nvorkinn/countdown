"""A store in a JSON file, for development: `REDIS_URL=file:///path/to/store.json` instead of a Redis.

reader._connect hands back connect()'s client for a `file:` URL, and that is the only place that knows: everything else
reads the store as it would Redis. It's a fakeredis client (the tests' store) seeded from the file, so every command
the renderer uses (pipelines, scripts, BRPOP) works as it does on Redis.

The file is read-only input, which you edit by hand. It's loaded on first use and again whenever it changes (before
the next command, so a running renderer sees a save), replacing the keys it held before and leaving any other key
alone. Nothing is written back, so what the renderer writes (frames, the job queue) lives in its process and is gone
when it ends. Nor is it shared between processes: a worker and a ticker each have a store of their own.

The file is one object, key -> entry. An entry that is a plain JSON value is a string: an object, array or number is
stored as its JSON text, which is what the providers write (so `v1:sub:tfl` reads as the object it is); a JSON string
is stored as it is. Anything else is a typed entry, `{"$type": ..., "$value": ..., "$ttl": seconds}` (`$ttl` is
optional: a key lives until the file changes):

    "$type": "hash"    "$value": {"field": "text"}
    "$type": "list"    "$value": ["head", "next", ...]
    "$type": "set"     "$value": ["member", ...]
    "$type": "bytes"   "$value": "<base64>"       (the covers, which aren't text)

A hash value or a list or set member that isn't text (a stored frame) is `{"$bytes": "<base64>"}`.

dump() writes a store's keys this way (without their TTLs: dev data that expired as you looked at it would be no help).
"""

import base64
import binascii
import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

SCHEME = "file"

_TYPE = "$type"
_VALUE = "$value"
_TTL = "$ttl"
_BYTES = "$bytes"

_MISSING_FAKEREDIS = (
    "REDIS_URL names a file, which needs fakeredis[lua]: it's in the dev dependency group, so run from the "
    "workspace with `uv run` (and it isn't installed in the images, where the store is Redis)"
)


class StoreFileError(ValueError):
    """The file isn't a store: not JSON, or an entry that's none of the kinds above."""


class _Source:
    """One file and the fake server holding it: every client for the same file shares them."""

    def __init__(self, path: Path) -> None:
        import fakeredis  # noqa: PLC0415 (only a development store needs it)

        self.path = path
        self.server = fakeredis.FakeServer()
        # The side that writes the file's keys in: bytes in, whatever the clients' decoding.
        self._loader = fakeredis.FakeRedis(server=self.server)
        self._mtime_ns: int | None = None
        self._loaded: set[bytes] = set()
        self._lock = threading.Lock()

    def sync(self) -> None:
        """Load the file if it's new or has changed. Raises if it can't be read, or isn't a store (and tries again)."""
        mtime_ns = self.path.stat().st_mtime_ns
        with self._lock:
            if mtime_ns == self._mtime_ns:
                return
            entries = _parse(self.path)
            if self._loaded:
                self._loader.delete(*self._loaded)
            for key, entry in entries.items():
                _write(self._loader, key, entry)
            self._loaded = {key.encode() for key in entries}
            self._mtime_ns = mtime_ns
            logger.info("Loaded %d keys from the store file %s", len(entries), self.path)


_sources: dict[Path, _Source] = {}
_sources_lock = threading.Lock()


def path_from_url(url: str) -> Path:
    """Return the file a `file:` URL names: `file:///abs/store.json`, or `file:store.json` for one beside the cwd."""
    parsed = urlparse(url)
    if parsed.scheme != SCHEME or parsed.netloc not in {"", "localhost"} or not parsed.path:
        msg = f"{url!r} isn't a file URL: expected file:///absolute/path/store.json or file:relative/store.json"
        raise ValueError(msg)
    return Path(unquote(parsed.path)).resolve()


def is_file_url(url: str) -> bool:
    """Return whether REDIS_URL names a store file rather than a Redis."""
    return urlparse(url).scheme == SCHEME


def connect(url: str, *, decode_responses: bool) -> redis.Redis:
    """Return a client for the store file `url` names, which reloads the file when it changes.

    Raises FileNotFoundError if there's no such file (an empty one, `{}`, is an empty store), and StoreFileError if it
    isn't a store: now, so a wrong path or a typo shows at start, not in the first render.
    """
    try:
        import fakeredis  # noqa: PLC0415
    except ImportError as error:
        raise RuntimeError(_MISSING_FAKEREDIS) from error

    path = path_from_url(url)
    with _sources_lock:
        source = _sources.get(path)
        if source is None:
            source = _sources[path] = _Source(path)
    source.sync()

    class FileRedis(fakeredis.FakeRedis):
        """A fake Redis that first loads whatever changed in the file."""

        def execute_command(self, *args: Any, **options: Any) -> Any:  # noqa: ANN401 (redis-py's own signature)
            source.sync()
            return super().execute_command(*args, **options)

    return FileRedis(server=source.server, decode_responses=decode_responses)


def dump(client: redis.Redis) -> dict[str, Any]:
    """Return every key of `client`'s Redis as a file's entries, keys in order: write it with json.dumps(indent=2).

    A string that is JSON becomes the JSON, so it can be read and edited; anything that isn't text becomes bytes. A
    key that's none of a string, hash, list or set (a stream) is left out, with a warning.
    """
    entries: dict[str, Any] = {}
    for raw_key in sorted(client.scan_iter()):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        kind = client.type(raw_key)
        kind = kind.decode() if isinstance(kind, bytes) else kind
        if kind not in {"string", "hash", "list", "set"}:
            logger.warning("Left out %s: a %s, which the store file has no entry for", key, kind)
            continue
        entries[key] = _dump_entry(client, raw_key, kind)
    return entries


def _dump_entry(client: redis.Redis, key: str | bytes, kind: str) -> Any:  # noqa: ANN401
    match kind:
        case "string":
            return _dump_string(client.get(key) or "")
        case "hash":
            return {_TYPE: "hash", _VALUE: {_text(f): _member(v) for f, v in client.hgetall(key).items()}}
        case "list":
            return {_TYPE: "list", _VALUE: [_member(v) for v in client.lrange(key, 0, -1)]}
        case "set":
            return {_TYPE: "set", _VALUE: sorted((_member(v) for v in client.smembers(key)), key=str)}
        case _:
            raise AssertionError(kind)


def _dump_string(value: str | bytes) -> Any:  # noqa: ANN401
    raw = value if isinstance(value, bytes) else value.encode()
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        return {_TYPE: "bytes", _VALUE: base64.b64encode(raw).decode()}
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    # Only an object or array is shown as JSON (its whitespace is lost, which no reader minds): a number or a word
    # stays the text it is, so that it loads back as the same string.
    return parsed if isinstance(parsed, dict | list) else text


def _text(value: str | bytes) -> str:
    try:
        return value.decode() if isinstance(value, bytes) else value
    except UnicodeDecodeError as error:
        msg = "a hash field isn't text, which the store file can't show"
        raise StoreFileError(msg) from error


def _member(value: str | bytes) -> str | dict[str, str]:
    """Return a hash value or list or set member for the file: its text, or its base64 if it isn't text."""
    if isinstance(value, str):
        return value
    try:
        return value.decode()
    except UnicodeDecodeError:
        return {_BYTES: base64.b64encode(value).decode()}


def _unmember(key: str, value: object) -> bytes:
    """Return the bytes of a hash value or list or set member from the file: text, or {"$bytes": base64}."""
    match value:
        case str():
            return value.encode()
        case {"$bytes": str(encoded)}:
            try:
                return base64.b64decode(encoded, validate=True)
            except binascii.Error as error:
                msg = f"{key!r}: not base64: {error}"
                raise StoreFileError(msg) from error
        case _:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _parse(path: Path) -> dict[str, Any]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        msg = f"{path} isn't JSON: {error}"
        raise StoreFileError(msg) from error
    if not isinstance(entries, dict):
        msg = f"{path} must hold one JSON object, key -> entry"
        raise StoreFileError(msg)
    return entries


def _write(loader: redis.Redis, key: str, entry: Any) -> None:  # noqa: ANN401
    loader.delete(key)
    ttl = None
    if isinstance(entry, dict) and _TYPE in entry:
        kind, value, ttl = entry[_TYPE], entry.get(_VALUE), entry.get(_TTL)
        _write_typed(loader, key, kind, value)
    elif isinstance(entry, str):
        loader.set(key, entry.encode())
    else:
        loader.set(key, json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode())
    if ttl is not None:
        loader.expire(key, ttl)


def _write_typed(loader: redis.Redis, key: str, kind: object, value: Any) -> None:  # noqa: ANN401
    match kind, value:
        case "hash", dict():
            if value:
                loader.hset(key, mapping={field: _unmember(key, member) for field, member in value.items()})
        case "list", list():
            if value:
                loader.rpush(key, *[_unmember(key, member) for member in value])
        case "set", list():
            if value:
                loader.sadd(key, *[_unmember(key, member) for member in value])
        case "bytes", str():
            try:
                loader.set(key, base64.b64decode(value, validate=True))
            except binascii.Error as error:
                msg = f"{key!r}: not base64: {error}"
                raise StoreFileError(msg) from error
        case _:
            msg = f"{key!r}: a {kind!r} entry with a {type(value).__name__} value isn't a hash, list, set or bytes"
            raise StoreFileError(msg)
