"""Optional link to a separate Music Grabber instance: ask it for a missing track.

BPM Tagger never downloads anything itself. It sends Music Grabber a request over
its integration API (``/api/v1``, an API key with the ``request`` scope) and shows
the request's state. When Music Grabber files the track into the shared music
folder, the watcher picks it up like any other new file; once the file is in the
library, the playlist row that asked for it is linked to it.

Server to server only: the API key lives in the config (env or settings.json,
masked in the Settings API) and never reaches a browser.

A request is identified by a ``source_ref``: ``pt:<playlist_track_id>`` for a
playlist's missing row, ``dz:<deezer_track_id>`` for a catalogue track (Related
panel). Music Grabber treats the same ref as idempotent, and so does this store.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

TIMEOUT = 10
REFRESH_AFTER_S = 15          # don't re-ask Music Grabber about one request more often
MAX_BATCH = 100               # one request call per track; keep a bulk click bounded
TERMINAL = {"done", "failed", "skipped", "cancelled", "in_library"}


class GrabberError(Exception):
    def __init__(self, code: str, message: str = "", http: int = 502):
        super().__init__(message or code)
        self.code, self.http = code, http


# ── config ────────────────────────────────────────────────────────────────────
def clean_url(raw) -> str:
    """An http(s) base URL without a trailing slash, or "" when it isn't one."""
    url = str(raw or "").strip().rstrip("/")
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return url


def configured(config: dict) -> bool:
    return bool(clean_url(config.get("music_grabber_url")) and config.get("music_grabber_api_key"))


def public_url(config: dict) -> str:
    """Where a browser opens Music Grabber (links), which may differ from the
    internal address the server calls (e.g. a Docker service name)."""
    return clean_url(config.get("music_grabber_public_url")) or clean_url(config.get("music_grabber_url"))


# ── HTTP client ───────────────────────────────────────────────────────────────
class Client:
    def __init__(self, config: dict, session: Optional[requests.Session] = None):
        self.base = clean_url(config.get("music_grabber_url"))
        self.key = str(config.get("music_grabber_api_key") or "")
        if not (self.base and self.key):
            raise GrabberError("not_configured", "Music Grabber isn't set up", 400)
        self.http = session or requests.Session()

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, self.base + path, timeout=TIMEOUT,
                                  headers={"Authorization": f"Bearer {self.key}"}, **kw)
        except requests.RequestException as exc:
            log.warning("Music Grabber unreachable: %s", type(exc).__name__)
            raise GrabberError("unreachable", "Music Grabber didn't answer") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 401:
            raise GrabberError("bad_key", "Music Grabber refused the API key", 502)
        if r.status_code == 403:
            raise GrabberError("missing_scope", "the API key needs the 'request' scope", 502)
        if r.status_code >= 400:
            raise GrabberError(str(body.get("error") or "grabber_error"),
                               str(body.get("message") or f"Music Grabber answered {r.status_code}"),
                               404 if r.status_code == 404 else 502)
        return body if isinstance(body, dict) else {}

    def request(self, payload: dict) -> dict:
        return self._call("POST", "/api/v1/requests", json=payload)

    def statuses(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        return self._call("GET", "/api/v1/requests",
                          params={"ids": ",".join(str(i) for i in ids)}).get("requests") or []

    def check(self) -> dict:
        """Prove the URL, the key and its scope: an empty status query answers
        ``no_ids`` (400) only after the key and the 'request' scope passed."""
        try:
            self._call("GET", "/api/v1/requests")
        except GrabberError as e:
            if e.code == "no_ids":
                return {"ok": True}
            raise
        return {"ok": True}


# ── the request store (created on first use; not part of the core schema) ─────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS grabber_requests (
    source_ref        TEXT PRIMARY KEY,
    request_id        INTEGER,
    status            TEXT,
    needs_choice      INTEGER DEFAULT 0,
    path              TEXT,
    error             TEXT,
    title             TEXT,
    artist            TEXT,
    playlist_track_id INTEGER,
    requested_by      TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    checked_at        REAL
)"""
_FIELDS = ("source_ref", "request_id", "status", "needs_choice", "path", "error", "title", "artist",
           "playlist_track_id", "requested_by", "created_at", "updated_at")


def _ensure(db) -> None:
    with db._connect() as conn:
        conn.execute(_SCHEMA)
        conn.commit()


def _view(row: dict) -> dict:
    out = {k: row.get(k) for k in _FIELDS}
    out["needs_choice"] = bool(out["needs_choice"])
    out["active"] = (row.get("status") or "") not in TERMINAL
    return out


def _rows(db, refs: list[str]) -> dict:
    if not refs:
        return {}
    _ensure(db)
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM grabber_requests WHERE source_ref IN ({','.join('?' * len(refs))})", refs).fetchall()
    return {r["source_ref"]: dict(r) for r in rows}


def _save(db, ref: str, **fields) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _ensure(db)
    with db._connect() as conn:
        exists = conn.execute("SELECT 1 FROM grabber_requests WHERE source_ref=?", (ref,)).fetchone()
        fields["updated_at"] = now
        if exists:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE grabber_requests SET {sets} WHERE source_ref=?", (*fields.values(), ref))
        else:
            fields.setdefault("created_at", now)
            cols = ["source_ref", *fields]
            conn.execute(f"INSERT INTO grabber_requests ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         (ref, *fields.values()))
        conn.commit()


def states(db, refs: list[str]) -> dict:
    return {ref: _view(row) for ref, row in _rows(db, refs).items()}


# ── operations ────────────────────────────────────────────────────────────────
def payload_for_playlist_track(pt: dict) -> dict:
    """What Music Grabber needs to find a playlist row: its ISRC when the row has
    one (exact), else title + artist (+ album, duration)."""
    out = {"title": pt.get("title") or "", "artist": pt.get("artist") or ""}
    if pt.get("album"):
        out["album"] = pt["album"]
    if isinstance(pt.get("duration_ms"), int):
        out["duration_ms"] = pt["duration_ms"]
    if pt.get("isrc"):
        out["isrc"] = pt["isrc"]
    return out


def request_track(db, config: dict, ref: str, payload: dict, *, who: str,
                  title: str = "", artist: str = "", playlist_track_id: Optional[int] = None,
                  client: Optional[Client] = None) -> dict:
    """Ask Music Grabber for one track and remember the answer under ``ref``.

    A ref that is already being fetched is not sent again; a failed one is."""
    prev = _rows(db, [ref]).get(ref)
    if prev and prev.get("status") not in ("failed", "skipped", "cancelled"):
        return _view(prev)
    client = client or Client(config)
    answer = client.request({**payload, "source_ref": f"bpm-tagger:{ref}", "requested_for": who})
    result = answer.get("result")
    status = "in_library" if result == "in_library" else (answer.get("status") or "pending")
    _save(db, ref, request_id=answer.get("id"), status=status, needs_choice=0, path=None, error=None,
          title=title or payload.get("title") or "", artist=artist or payload.get("artist") or "",
          playlist_track_id=playlist_track_id, requested_by=who, checked_at=time.time())
    return _view(_rows(db, [ref])[ref])


def refresh(db, config: dict, refs: list[str], client: Optional[Client] = None, *, force: bool = False) -> dict:
    """Bring the active requests among ``refs`` up to date (one call to Music
    Grabber), link finished files to the playlist rows that asked for them, and
    return every known state."""
    rows = _rows(db, refs)
    now = time.time()
    due = {r["request_id"]: ref for ref, r in rows.items()
           if r.get("request_id") and (r.get("status") or "") not in TERMINAL
           and (force or now - float(r.get("checked_at") or 0) >= REFRESH_AFTER_S)}
    if due and configured(config):
        try:
            got = (client or Client(config)).statuses(sorted(due))
        except GrabberError as e:
            log.info("Music Grabber status refresh skipped: %s", e.code)
            got = None
        if got is not None:
            seen = set()
            for item in got:
                ref = due.get(item.get("id"))
                if not ref:
                    continue
                seen.add(ref)
                _save(db, ref, status=item.get("status") or "pending",
                      needs_choice=int(bool(item.get("needs_choice"))), path=item.get("path"),
                      error=item.get("error"), checked_at=now)
            for ref in set(due.values()) - seen:      # Music Grabber no longer has it (deleted there)
                _save(db, ref, status="failed", error="no longer in Music Grabber's queue", checked_at=now)
            rows = _rows(db, refs)
    for ref, row in rows.items():
        _link_finished(db, config, row)
    return states(db, refs)


def _link_finished(db, config: dict, row: dict) -> None:
    """A done request whose file the library already holds: point the playlist
    row at it, so it reads as *have* without waiting for the next sync."""
    if row.get("status") != "done" or not row.get("path") or not row.get("playlist_track_id"):
        return
    root = os.path.realpath(config.get("music_dir") or "")
    full = os.path.realpath(os.path.join(root, row["path"]))
    if not root or os.path.commonpath([root, full]) != root:
        return
    track = db.get_track(full)
    if track and track.get("status") != "deleted":
        db.set_playlist_track_match(int(row["playlist_track_id"]), "have", full)
