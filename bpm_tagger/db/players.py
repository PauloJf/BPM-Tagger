"""Player-user accounts (Phase 5): local run users scoped to playlists."""

from datetime import datetime, timezone
from typing import Optional


class PlayersMixin:

    def _player_row(self, r) -> dict:
        """Serialize a players row with booleans and its scoped playlist ids."""
        d = dict(r)
        d["full_access"] = bool(d.get("full_access"))
        d["enabled"] = bool(d.get("enabled"))
        # accent_hue is an int or None (never coerced to bool).
        return d

    def list_players(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM players ORDER BY username COLLATE NOCASE").fetchall()
            out = []
            for r in rows:
                d = self._player_row(r)
                d["playlist_ids"] = [pr["playlist_id"] for pr in conn.execute(
                    "SELECT playlist_id FROM player_playlists WHERE player_id = ?",
                    (r["id"],)).fetchall()]
                out.append(d)
        return out

    def get_player(self, player_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        return self._player_row(row) if row else None

    def get_player_by_username(self, username: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM players WHERE username = ?",
                               (str(username).strip().lower(),)).fetchone()
        return self._player_row(row) if row else None

    def add_player(self, username: str, password_hash: str, full_access: bool = False,
                   playlist_ids: Optional[list] = None) -> int:
        """Create a player user. Raises sqlite3.IntegrityError on a duplicate username
        (usernames are stored lowercased). Associates the given playlist ids."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO players (username, password_hash, full_access, enabled, created_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (str(username).strip().lower(), password_hash, int(bool(full_access)), now))
            pid = cur.lastrowid
            self._set_player_playlists(conn, pid, playlist_ids or [])
            conn.commit()
            return pid

    def update_player(self, player_id: int, *, full_access: Optional[bool] = None,
                      enabled: Optional[bool] = None,
                      playlist_ids: Optional[list] = None) -> None:
        with self._connect() as conn:
            sets, vals = [], []
            if full_access is not None:
                sets.append("full_access = ?"); vals.append(int(bool(full_access)))
            if enabled is not None:
                sets.append("enabled = ?"); vals.append(int(bool(enabled)))
            if sets:
                vals.append(player_id)
                conn.execute(f"UPDATE players SET {', '.join(sets)} WHERE id = ?", vals)
            if playlist_ids is not None:
                self._set_player_playlists(conn, player_id, playlist_ids)
            conn.commit()

    def set_player_accent(self, player_id: int, hue: Optional[int]) -> None:
        """Persist a player user's chosen accent hue (0–360), or clear it (None →
        fall back to the client default). Stored per-account so the accent follows
        the user across browsers/devices."""
        val = None if hue is None else max(0, min(360, int(hue)))
        with self._connect() as conn:
            conn.execute("UPDATE players SET accent_hue = ? WHERE id = ?", (val, player_id))
            conn.commit()

    def set_player_password(self, player_id: int, password_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE players SET password_hash = ? WHERE id = ?",
                         (password_hash, player_id))
            conn.commit()

    def touch_player_login(self, player_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("UPDATE players SET last_login_at = ? WHERE id = ?", (now, player_id))
            conn.commit()

    def delete_player(self, player_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM player_playlists WHERE player_id = ?", (player_id,))
            conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
            conn.commit()

    def _set_player_playlists(self, conn, player_id: int, playlist_ids: list) -> None:
        conn.execute("DELETE FROM player_playlists WHERE player_id = ?", (player_id,))
        for pid in {int(x) for x in playlist_ids}:
            conn.execute(
                "INSERT OR IGNORE INTO player_playlists (player_id, playlist_id) VALUES (?, ?)",
                (player_id, pid))

    def set_player_playlists(self, player_id: int, playlist_ids: list) -> None:
        with self._connect() as conn:
            self._set_player_playlists(conn, player_id, playlist_ids)
            conn.commit()

    def playlist_ids_for_player(self, player_id: int) -> set:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT playlist_id FROM player_playlists WHERE player_id = ?",
                (player_id,)).fetchall()
        return {r["playlist_id"] for r in rows}

    def list_playlists_for_player(self, player_id: int) -> list[dict]:
        """The subset of list_playlists() a restricted player is scoped to."""
        allowed = self.playlist_ids_for_player(player_id)
        return [p for p in self.list_playlists() if p["id"] in allowed]
