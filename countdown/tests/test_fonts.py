import http.client
import io

import pytest

from countdown_core.utils import utils

PARTIAL = bytes(range(8))  # what a dropped connection got of the font


def test_google_symbols_downloads_once_on_first_use_and_then_reads_the_cached_file(tmp_path, monkeypatch):
    stand_in = (utils.FONTS_DIR / "Ubuntu-Regular.ttf").read_bytes()
    monkeypatch.setattr(utils, "FONTS_DIR", tmp_path / "fonts")
    urls = []

    def fake_urlopen(url, timeout):
        urls.append(url)
        return io.BytesIO(stand_in)

    monkeypatch.setattr(utils.urllib.request, "urlopen", fake_urlopen)
    utils.google_symbols.cache_clear()
    try:
        utils.google_symbols()
        assert len(urls) == 1
        assert [p.name for p in (tmp_path / "fonts").iterdir()] == ["MaterialSymbolsOutlined[FILL,GRAD,opsz,wght].ttf"]

        utils.google_symbols.cache_clear()  # as if the app restarted: the file on disk is reused
        utils.google_symbols()
        assert len(urls) == 1
    finally:
        utils.google_symbols.cache_clear()  # don't leave the stand-in cached for other tests


def test_a_download_that_drops_part_way_is_tried_again(monkeypatch):
    attempts = []

    def flaky_urlopen(url, timeout):
        attempts.append(url)
        if len(attempts) < 3:
            raise http.client.IncompleteRead(PARTIAL, 1000)
        return io.BytesIO(b"a whole font")

    monkeypatch.setattr(utils.urllib.request, "urlopen", flaky_urlopen)
    assert utils._download("https://example.invalid/font.ttf") == b"a whole font"
    assert len(attempts) == 3


def test_a_download_that_keeps_failing_gives_up_with_its_error(monkeypatch):
    def broken_urlopen(url, timeout):
        raise http.client.IncompleteRead(PARTIAL, 1000)

    monkeypatch.setattr(utils.urllib.request, "urlopen", broken_urlopen)
    with pytest.raises(http.client.IncompleteRead):
        utils._download("https://example.invalid/font.ttf")
