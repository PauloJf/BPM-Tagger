"""Soft-delete trash.

Duplicate resolution moves the unwanted file here instead of deleting it, so a
mistaken delete is recoverable until the user purges. The trash lives under the
data dir (next to the DB), i.e. *outside* MUSIC_DIR — so Navidrome never indexes
trashed files, and a rescan simply drops the removed original from the library.
"""

import logging
import os
import shutil

log = logging.getLogger(__name__)


def trash_dir(config: dict) -> str:
    data = os.path.dirname(os.path.abspath(config["db_path"]))
    return os.path.join(data, "trash")


def move_to_trash(config: dict, file_path: str) -> str:
    """Move a file into the trash, disambiguating name collisions. Returns the
    new path."""
    d = trash_dir(config)
    os.makedirs(d, exist_ok=True)
    base = os.path.basename(file_path)
    dest = os.path.join(d, base)
    stem, ext = os.path.splitext(base)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(d, f"{stem} ({n}){ext}")
        n += 1
    shutil.move(file_path, dest)
    log.info("Trashed %s", base)
    return dest


def trash_stats(config: dict) -> dict:
    """Current trash contents: file count and total bytes."""
    d = trash_dir(config)
    count = 0
    total = 0
    if os.path.isdir(d):
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                if os.path.isfile(p):
                    count += 1
                    total += os.path.getsize(p)
            except OSError:
                pass
    return {"count": count, "bytes": total}


def purge_trash(config: dict) -> dict:
    """Permanently delete everything in the trash. Returns what was removed."""
    d = trash_dir(config)
    removed = 0
    freed = 0
    if os.path.isdir(d):
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                size = os.path.getsize(p) if os.path.isfile(p) else 0
                if os.path.isfile(p):
                    os.remove(p)
                else:
                    shutil.rmtree(p, ignore_errors=True)
                removed += 1
                freed += size
            except OSError as exc:
                log.warning("Could not purge %s: %s", name, exc)
    return {"removed": removed, "bytes_freed": freed}
