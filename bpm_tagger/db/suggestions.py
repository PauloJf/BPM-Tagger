"""Deezer-derived artist/track suggestions + the artist index feed."""

import json

from datetime import datetime, timezone
from typing import Optional

class SuggestionsMixin:
    # ══════════════════════════════════════════════════════════════════════════
    # Suggestions (Deezer-derived; owned by the grabber's SuggestionsEngine)
    # ══════════════════════════════════════════════════════════════════════════

    def get_artist_index_rows(self) -> list[dict]:
        """(artist, album_artist, starred) for every non-deleted track — feeds
        the suggestions seed selection and the library-artist presence map."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT artist, album_artist, starred FROM tracks WHERE status != 'deleted'"
            ).fetchall()
        return [dict(r) for r in rows]

    def replace_suggestions(self, artists: list[dict], tracks: list[dict]) -> None:
        """Atomically replace the whole suggestions set (one transaction).

        Artist rows carry ``have_tracks``/``seeds``; track rows carry
        ``artist``/``album``/``duration_ms``/``preview_url``. Both are stamped
        with a single ``computed_at`` so staleness is a table-wide check."""
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for a in artists:
            rows.append(("artist", str(a.get("dz_id")), a.get("name"), "", "", None,
                         a.get("image_url", ""), "", a.get("score", 0),
                         int(a.get("have_tracks", 0) or 0),
                         json.dumps(a.get("seeds") or []), now))
        for t in tracks:
            rows.append(("track", str(t.get("dz_track_id")), t.get("title"),
                         t.get("artist", ""), t.get("album", ""), t.get("duration_ms"),
                         t.get("cover_url", ""), t.get("preview_url", ""),
                         t.get("score", 0), 0, json.dumps(t.get("seeds") or []), now))
        with self._connect() as conn:
            conn.execute("DELETE FROM suggestions")
            if rows:
                conn.executemany("""
                    INSERT INTO suggestions
                        (kind, dz_id, name, artist, album, duration_ms, image_url,
                         preview_url, score, have_tracks, seeds, computed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
            conn.commit()

    def get_suggestions(self, kind: str) -> list[dict]:
        """Stored suggestions of a kind ('artist' | 'track'), best score first.
        ``seeds`` is decoded from JSON into a list."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM suggestions WHERE kind=? ORDER BY score DESC, id ASC",
                (kind,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["seeds"] = json.loads(d.get("seeds") or "[]")
            except (ValueError, TypeError):
                d["seeds"] = []
            out.append(d)
        return out

    def suggestions_computed_at(self) -> Optional[str]:
        """When the stored suggestions were last computed (max computed_at), or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(computed_at) AS c FROM suggestions").fetchone()
        return row["c"] if row and row["c"] else None

    def mark_suggestion_queued(self, suggestion_id: int) -> None:
        """Stamp a suggestion row as queued (survives until the next refresh)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("UPDATE suggestions SET queued_at=? WHERE id=?",
                         (now, suggestion_id))
            conn.commit()

    def dismiss_suggestion(self, kind: str, key: str) -> None:
        """Persist a dismissal and prune any matching suggestion rows.

        Tracks are keyed by Deezer track id (matches ``dz_id`` directly); artists
        by normalized name, so matching rows are found by normalizing each stored
        artist name."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO suggestion_dismissed (kind, key, dismissed_at) "
                "VALUES (?,?,?)", (kind, key, now))
            if kind == "track":
                conn.execute("DELETE FROM suggestions WHERE kind='track' AND dz_id=?", (key,))
            elif kind == "artist":
                from ..grabber.matching import normalize_artist
                rows = conn.execute(
                    "SELECT id, name FROM suggestions WHERE kind='artist'").fetchall()
                ids = [(r["id"],) for r in rows if normalize_artist(r["name"]) == key]
                if ids:
                    conn.executemany("DELETE FROM suggestions WHERE id=?", ids)
            conn.commit()

    def get_dismissed_suggestion_keys(self, kind: str) -> set:
        """The set of dismissed keys for a kind ('artist' | 'track')."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key FROM suggestion_dismissed WHERE kind=?", (kind,)).fetchall()
        return {r["key"] for r in rows}
