"""Folder view of the library, for clients that browse by directory
(getIndexes / getMusicDirectory — DSub, older Subsonic apps).

Built from the DB's file paths, not a filesystem walk: it lists exactly what the
library holds, never anything else under MUSIC_DIR, and a player user's view is
built from only the tracks their playlists hold.
"""

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import ids

_TTL = 10.0
_MAX_CACHED_SCOPES = 32


@dataclass
class Folder:
    rel: str                                         # "" = root, "/"-separated
    subdirs: set = field(default_factory=set)        # child rel paths
    files: list = field(default_factory=list)        # absolute file paths

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1] if self.rel else ""

    @property
    def parent_rel(self) -> Optional[str]:
        if not self.rel:
            return None
        return self.rel.rsplit("/", 1)[0] if "/" in self.rel else ""


def _rel(path: str, music_dir: str) -> Optional[str]:
    try:
        rel = os.path.relpath(path, music_dir)
    except ValueError:
        return None
    rel = rel.replace(os.sep, "/")
    return None if rel.startswith("../") or rel == ".." else rel


class DirIndex:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict = {}   # scope key → (built_at, {dir_id: Folder})

    def folders(self, db, music_dir: str, scope) -> dict:
        key = (db.db_path, music_dir, None if scope is None else tuple(sorted(scope)))
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < _TTL:
                return hit[1]
        tree: dict[str, Folder] = {"": Folder("")}
        for path in db.subsonic_all_paths(scope):
            rel = _rel(path, music_dir)
            if not rel:
                continue
            parts = rel.split("/")
            for depth in range(len(parts) - 1):
                parent = "/".join(parts[:depth])
                child = "/".join(parts[:depth + 1])
                tree.setdefault(parent, Folder(parent)).subdirs.add(child)
                tree.setdefault(child, Folder(child))
            tree.setdefault("/".join(parts[:-1]), Folder("/".join(parts[:-1]))).files.append(path)
        by_id = {ids.dir_id(f.rel): f for f in tree.values()}
        with self._lock:
            if len(self._cache) >= _MAX_CACHED_SCOPES:
                self._cache.clear()
            self._cache[key] = (time.monotonic(), by_id)
        return by_id

    def get(self, db, music_dir: str, scope, did: str) -> Optional[Folder]:
        return self.folders(db, music_dir, scope).get(did)

    def root(self, db, music_dir: str, scope) -> Folder:
        return self.folders(db, music_dir, scope)[ids.dir_id("")]


dir_index = DirIndex()
