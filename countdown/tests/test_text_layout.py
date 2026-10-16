from PIL import features


def test_raqm_text_layout_is_available():
    """The golden images are drawn with Pillow's Raqm layout, which Pillow only uses when
    libfribidi is installed. Without it Pillow silently falls back to basic layout, and
    every snapshot with text fails with a small, confusing mismatch.
    """
    assert features.check_feature("raqm"), (
        "Pillow's Raqm text layout isn't available, so text won't match the golden images. "
        "Install fribidi: `sudo apt-get install libfribidi0` on Linux, or on macOS "
        "`brew install fribidi` plus "
        "`sudo ln -s /opt/homebrew/lib/libfribidi.0.dylib /usr/local/lib/`."
    )
