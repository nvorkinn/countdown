"""Draw screens from a JSON store file, with no Redis: `uv run dev/render_local.py [device id ...]`, from the repo root.

The data is a store file (core/src/countdown_core/store/file_store.py): by default local-only/store.json, or the
one --store names. Edit it, run this again, and see the frame it draws in local-only/renders/<device id>.png (or
--out). With no device id, every screen the file lists in `v1:render:screens` is drawn.

It always reads the file, whatever REDIS_URL is set to in the shell, so it can't draw into a real Redis; and it writes
nothing but the images.
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_STORE = REPO / "local-only" / "store.json"
DEFAULT_OUT = REPO / "local-only" / "renders"


def main() -> int:
    """Draw each screen and say where its image is. Return the exit status: 0, or 1 if one drew nothing."""
    args = _arguments()
    store_file = args.store.resolve()
    if not store_file.is_file():
        print(f"No store file at {store_file}: pass --store, or make one (file_store.dump)", file=sys.stderr)
        return 2
    os.environ["REDIS_URL"] = store_file.as_uri()
    os.environ.pop("REDIS_PASSWORD", None)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    # Imported only now: reader.store() reads REDIS_URL the first time it's asked.
    from countdown_core.store import reader, render_queue  # noqa: PLC0415
    from countdown_renderer import render  # noqa: PLC0415

    device_ids = args.device_ids or sorted(render_queue.screens(reader.require_store()))
    if not device_ids:
        print(f"No device id given, and {store_file} lists no screens in v1:render:screens", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for device_id in device_ids:
        outcome = render.render(device_id, datetime.now(UTC))
        if outcome.frame is None:
            print(f"{device_id}: {outcome.result} (nothing drawn: does the file have v1:config:{device_id}?)")
            failed = True
            continue
        path = args.out / f"{device_id}.png"
        outcome.frame.image.save(path)
        print(f"{device_id}: {outcome.stage} -> {path}")
    return 1 if failed else 0


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("device_ids", nargs="*", metavar="device id", help="screens to draw (default: all in the file)")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE, help="the store file (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where the images go (default: %(default)s)")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
