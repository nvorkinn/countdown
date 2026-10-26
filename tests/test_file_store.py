import json
import os

import fakeredis
import pytest

from countdown_shared.store import file_store, reader, render_queue


@pytest.fixture
def store_file(tmp_path):
    return tmp_path / "store.json"


def _write(path, entries):
    path.write_text(json.dumps(entries), encoding="utf-8")
    # A save inside the filesystem's timestamp granularity would look unchanged.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def _connect(path, *, decode_responses=True):
    return file_store.connect(path.as_uri(), decode_responses=decode_responses)


def test_a_json_object_is_stored_as_the_json_text_a_provider_writes(store_file):
    _write(store_file, {"v1:sub:tfl": {"stops": ["940GZZLUEAC"], "n": 1}})

    client = _connect(store_file)

    assert client.get("v1:sub:tfl") == '{"stops":["940GZZLUEAC"],"n":1}'


def test_a_json_string_is_stored_as_it_is(store_file):
    _write(store_file, {"v1:name": "plain text", "v1:count": 5})

    client = _connect(store_file)

    assert client.get("v1:name") == "plain text"
    assert client.get("v1:count") == "5"


def test_typed_entries_are_a_hash_a_list_a_set_and_bytes(store_file):
    _write(
        store_file,
        {
            "hash": {"$type": "hash", "$value": {"a": "1", "b": "2"}},
            "list": {"$type": "list", "$value": ["head", "next"]},
            "set": {"$type": "set", "$value": ["x", "y"]},
            "cover": {"$type": "bytes", "$value": "gP8="},
        },
    )

    client = _connect(store_file, decode_responses=False)

    assert client.hgetall("hash") == {b"a": b"1", b"b": b"2"}
    assert client.lrange("list", 0, -1) == [b"head", b"next"]
    assert client.smembers("set") == {b"x", b"y"}
    assert client.get("cover") == b"\x80\xff"


def test_the_text_and_the_binary_client_read_the_same_file(store_file):
    _write(store_file, {"key": "value"})

    assert _connect(store_file).get("key") == "value"
    assert _connect(store_file, decode_responses=False).get("key") == b"value"


def test_a_ttl_expires_the_key(store_file):
    _write(store_file, {"short": {"$type": "bytes", "$value": "YQ==", "$ttl": 30}, "long": "kept"})

    client = _connect(store_file)

    assert 0 < client.ttl("short") <= 30
    assert client.ttl("long") == -1


def test_a_saved_edit_is_loaded_before_the_next_command(store_file):
    _write(store_file, {"a": "1", "b": "2"})
    client = _connect(store_file)
    client.set("renderer:wrote", "this")

    _write(store_file, {"a": "changed", "c": "3"})

    assert client.get("a") == "changed"
    assert client.get("b") is None
    assert client.get("c") == "3"
    assert client.get("renderer:wrote") == "this"


def test_a_file_that_breaks_fails_the_command_then_recovers_when_fixed(store_file):
    _write(store_file, {"a": "1"})
    client = _connect(store_file)
    store_file.write_text("{not json", encoding="utf-8")
    os.utime(store_file, ns=(0, store_file.stat().st_mtime_ns + 2_000_000_000))

    with pytest.raises(file_store.StoreFileError, match="isn't JSON"):
        client.get("a")

    _write(store_file, {"a": "2"})
    assert client.get("a") == "2"


def test_what_the_renderer_uses_works_on_it(store_file):
    """Pipelines, scripts and a blocking pop: the render queue uses all three."""
    _write(store_file, {"v1:render:screens": {"$type": "hash", "$value": {"device-1": "slug"}}})
    client = _connect(store_file)

    assert render_queue.screens(client) == {"device-1": "slug"}
    assert client.brpop(["v1:render:jobs"], timeout=1) is None


@pytest.mark.parametrize(
    "entry",
    [{"$type": "stream", "$value": []}, {"$type": "hash", "$value": [1]}, {"$type": "bytes", "$value": "not base64!"}],
)
def test_an_entry_that_is_none_of_the_kinds_is_refused_at_connect(store_file, entry):
    _write(store_file, {"bad": entry})

    with pytest.raises(file_store.StoreFileError, match="'bad'"):
        _connect(store_file)


def test_a_missing_file_is_refused_at_connect(tmp_path):
    with pytest.raises(FileNotFoundError):
        _connect(tmp_path / "nowhere.json")


@pytest.mark.parametrize("url", ["file://remote-host/store.json", "file:", "redis://127.0.0.1"])
def test_a_url_that_names_no_local_file_is_refused(url):
    with pytest.raises(ValueError, match="isn't a file URL"):
        file_store.path_from_url(url)


def test_dump_writes_what_connect_reads(store_file):
    server = fakeredis.FakeServer()
    source = fakeredis.FakeRedis(server=server)
    source.set("json", '{"etag": "W/x", "n": 1}')
    source.set("text", "plain")
    source.set("number", "0")
    source.set("cover", b"\x80\xff")
    source.hset("hash", mapping={"a": "1", "frame": b"\x89PNG"})
    source.rpush("list", "one", "two")
    source.sadd("set", "m")

    dumped = file_store.dump(source)
    _write(store_file, dumped)
    client = _connect(store_file, decode_responses=False)

    assert dumped["json"] == {"etag": "W/x", "n": 1}
    assert dumped["number"] == "0"
    assert json.loads(client.get("json")) == {"etag": "W/x", "n": 1}
    assert client.get("text") == b"plain"
    assert client.get("number") == b"0"
    assert client.get("cover") == b"\x80\xff"
    assert dumped["hash"]["$value"]["frame"] == {"$bytes": "iVBORw=="}
    assert client.hgetall("hash") == {b"a": b"1", b"frame": b"\x89PNG"}
    assert client.lrange("list", 0, -1) == [b"one", b"two"]
    assert client.smembers("set") == {b"m"}


def test_the_reader_connects_to_a_file_url_and_to_nothing_else_changes(store_file, monkeypatch):
    _write(store_file, {"key": "value"})
    monkeypatch.setenv("REDIS_URL", store_file.as_uri())

    assert reader._connect(decode_responses=True).get("key") == "value"

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    assert reader._connect(decode_responses=True).connection_pool.connection_kwargs["port"] == 6399
