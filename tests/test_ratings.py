"""Per-account ratings + dislikes (docs/plans/ratings-weighted-picking.md, Phase 1).

Covers the one-time migration from tracks.starred / tracks.disliked, the admin
projection kept in step, the derived star, per-account isolation, the guest
403, and cleanup on track / player deletion.
"""

import os
import sqlite3
from urllib.parse import quote

import pytest
from werkzeug.security import generate_password_hash

from bpm_tagger.db import BPMDatabase
from bpm_tagger.db.ratings import normalize_rating, star_to_rating


def _seed(db_path, music_dir, rows):
    """rows: (name, bpm, starred, disliked)."""
    conn = sqlite3.connect(db_path)
    for name, bpm, starred, disliked in rows:
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, bpm, starred, disliked, status) "
            "VALUES (?, ?, 'Artist', ?, ?, ?, 'done')",
            (f"{music_dir}/{name}.mp3", name, bpm, starred, disliked))
    conn.commit()
    conn.close()


def _app(base_config, **over):
    from bpm_tagger.config import build_config
    from bpm_tagger.web.app import create_app

    cfg = build_config()
    cfg.update({k: base_config[k] for k in ("db_path", "music_dir", "ui_password", "ui_secret_key")})
    cfg["write_tags"] = False
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


def _login(client, **body):
    r = client.post("/api/login", json=body)
    assert r.status_code == 200, r.get_json()
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_normalize_rating():
    assert normalize_rating(None) is None
    assert normalize_rating(0) is None
    assert normalize_rating("") is None
    assert normalize_rating(3) == 3
    assert normalize_rating("5") == 5
    for bad in (6, -1, True, "x"):
        with pytest.raises(ValueError):
            normalize_rating(bad)


def test_star_to_rating():
    assert star_to_rating(None, True) == 4
    assert star_to_rating(2, True) == 4
    assert star_to_rating(5, True) == 5          # already starred: untouched
    assert star_to_rating(5, False) == 3
    assert star_to_rating(4, False) == 3
    assert star_to_rating(2, False) == 2         # not starred: untouched
    assert star_to_rating(None, False) is None


# ── migration ────────────────────────────────────────────────────────────────

def test_migration_seeds_admin_from_star_and_dislike(tmp_path):
    db_path = str(tmp_path / "old.db")
    # A pre-ratings DB: the old flags on tracks, no track_ratings table.
    BPMDatabase(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE track_ratings")
    conn.executemany("INSERT INTO tracks (file_path, status, starred, disliked) VALUES (?, 'done', ?, ?)",
                     [("/m/s.mp3", 1, 0), ("/m/d.mp3", 0, 1), ("/m/both.mp3", 1, 1), ("/m/none.mp3", 0, 0)])
    conn.commit()
    conn.close()

    db = BPMDatabase(db_path)
    assert db.get_mark("admin", "/m/s.mp3") == {"rating": 4, "disliked": False, "starred": True}
    assert db.get_mark("admin", "/m/d.mp3") == {"rating": None, "disliked": True, "starred": False}
    assert db.get_mark("admin", "/m/both.mp3") == {"rating": 4, "disliked": True, "starred": True}
    assert db.get_mark("admin", "/m/none.mp3")["rating"] is None
    with db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM track_ratings").fetchone()[0] == 3
    # Re-opening does not re-seed (the table already exists).
    db.set_rating("admin", "/m/s.mp3", None)
    BPMDatabase(db_path)
    assert db.get_mark("admin", "/m/s.mp3")["rating"] is None


# ── DB semantics ─────────────────────────────────────────────────────────────

def _db_with(tmp_path, names):
    db = BPMDatabase(str(tmp_path / "r.db"))
    _seed(db.db_path, "/m", [(n, 120.0, 0, 0) for n in names])
    return db


def _projection(db, path):
    with db._connect() as c:
        r = c.execute("SELECT starred, disliked FROM tracks WHERE file_path = ?", (path,)).fetchone()
    return r["starred"], r["disliked"]


def test_admin_projection_follows_rating_and_dislike(tmp_path):
    db = _db_with(tmp_path, ["a"])
    p = "/m/a.mp3"
    db.set_rating("admin", p, 5)
    assert _projection(db, p) == (1, 0)
    db.set_rating("admin", p, 3)
    assert _projection(db, p) == (0, 0)
    db.set_owner_disliked("admin", p, True)
    assert _projection(db, p) == (0, 1)
    assert db.get_mark("admin", p) == {"rating": 3, "disliked": True, "starred": False}
    # Clearing both deletes the row.
    db.set_rating("admin", p, None)
    db.set_owner_disliked("admin", p, False)
    with db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM track_ratings").fetchone()[0] == 0
    assert _projection(db, p) == (0, 0)


def test_player_marks_never_touch_projection_or_admin(tmp_path):
    db = _db_with(tmp_path, ["a"])
    p = "/m/a.mp3"
    db.set_rating("admin", p, 2)
    db.set_rating("player:1", p, 5)
    db.set_owner_disliked("player:1", p, True)
    assert _projection(db, p) == (0, 0)
    assert db.get_mark("admin", p) == {"rating": 2, "disliked": False, "starred": False}
    assert db.get_mark("player:1", p) == {"rating": 5, "disliked": True, "starred": True}
    assert db.get_mark("player:2", p)["rating"] is None


def test_set_starred_compat_goes_through_rating(tmp_path):
    db = _db_with(tmp_path, ["a"])
    p = "/m/a.mp3"
    db.set_starred(p, True)
    assert db.get_mark("admin", p)["rating"] == 4 and _projection(db, p)[0] == 1
    db.set_starred(p, False)
    assert db.get_mark("admin", p)["rating"] == 3 and _projection(db, p)[0] == 0


def test_star_sync_write_moves_admin_rating(tmp_path):
    db = _db_with(tmp_path, ["a"])
    p = "/m/a.mp3"
    db.set_star_synced(p, True, "nd-1")
    assert db.get_mark("admin", p)["rating"] == 4
    with db._connect() as c:
        r = c.execute("SELECT starred, starred_base, nd_song_id FROM tracks WHERE file_path = ?",
                      (p,)).fetchone()
    assert (r["starred"], r["starred_base"], r["nd_song_id"]) == (1, 1, "nd-1")
    db.set_rating("admin", p, 5)
    db.set_star_synced(p, True)          # already starred: the 5 is kept
    assert db.get_mark("admin", p)["rating"] == 5


def test_candidates_drop_only_the_owners_dislikes(tmp_path):
    db = _db_with(tmp_path, ["a", "b"])
    db.set_owner_disliked("player:1", "/m/a.mp3", True)
    db.set_owner_disliked("admin", "/m/b.mp3", True)
    paths = lambda owner: {t["file_path"] for t in db.get_run_candidates(None, owner=owner)}  # noqa: E731
    assert paths("admin") == {"/m/a.mp3"}
    assert paths("player:1") == {"/m/b.mp3"}
    assert paths("player:2") == {"/m/a.mp3", "/m/b.mp3"}
    assert paths("guest") == {"/m/a.mp3", "/m/b.mp3"}


def test_annotate_marks_new_rule(tmp_path):
    db = _db_with(tmp_path, ["a", "b", "c"])
    with db._connect() as c:
        c.execute("UPDATE tracks SET play_count = 3 WHERE file_path = '/m/b.mp3'")
        c.execute("INSERT INTO play_events (owner, file_path, played_at) "
                  "VALUES ('player:1', '/m/a.mp3', '2026-01-01')")
    db.set_rating("admin", "/m/c.mp3", 4)
    rows = lambda owner: {t["file_path"]: t for t in db.annotate_marks(db.get_run_candidates(None), owner)}  # noqa: E731
    admin = rows("admin")
    assert admin["/m/a.mp3"]["is_new"] is True           # never played anywhere
    assert admin["/m/b.mp3"]["is_new"] is False          # global play_count > 0
    assert admin["/m/c.mp3"]["is_new"] is False          # rated
    assert admin["/m/c.mp3"]["starred"] is True
    player = rows("player:1")
    assert player["/m/a.mp3"]["is_new"] is False         # the player's own play
    assert player["/m/b.mp3"]["is_new"] is True          # others' plays don't count
    assert player["/m/c.mp3"]["rating"] is None          # admin's rating never leaks
    assert all(t["is_new"] is False and t["rating"] is None for t in rows("guest").values())


def test_rating_distribution(tmp_path):
    db = _db_with(tmp_path, ["a", "b", "c", "d"])
    db.set_rating("admin", "/m/a.mp3", 5)
    db.set_rating("admin", "/m/b.mp3", 1)
    db.set_owner_disliked("admin", "/m/b.mp3", True)
    db.set_owner_disliked("admin", "/m/c.mp3", True)
    d = db.rating_distribution("admin")
    assert d["5"] == 1 and d["1"] == 1 and d["unrated"] == 2
    assert d["disliked"] == 2 and d["total"] == 4


def test_track_delete_cascades_and_player_delete_cleans_up(tmp_path):
    db = _db_with(tmp_path, ["a"])
    pid = db.add_player("runner", "hash")
    db.set_rating(f"player:{pid}", "/m/a.mp3", 5)
    db.set_rating("admin", "/m/a.mp3", 5)
    db.delete_player(pid)
    with db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM track_ratings WHERE owner LIKE 'player:%'").fetchone()[0] == 0
        c.execute("DELETE FROM tracks WHERE file_path = '/m/a.mp3'")
    with db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM track_ratings").fetchone()[0] == 0


# ── API ──────────────────────────────────────────────────────────────────────

def test_rating_api_roundtrip_and_validation(base_config):
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client, password="s3cret")
    _seed(base_config["db_path"], base_config["music_dir"], [("song", 120.0, 0, 0)])
    path = f"{base_config['music_dir']}/song.mp3"

    r = client.post("/api/track/rating", json={"path": path, "rating": 5}, headers=csrf)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "rating": 5, "disliked": False, "starred": True}
    track = client.get(f"/api/track?path={quote(path)}").get_json()["track"]
    assert track["rating"] == 5 and track["starred"] is True

    assert client.post("/api/track/rating", json={"path": path, "rating": 9},
                       headers=csrf).status_code == 400
    r = client.post("/api/track/rating", json={"path": path, "rating": None}, headers=csrf)
    assert r.get_json()["rating"] is None
    assert client.post("/api/track/rating", json={"path": path, "rating": 3}).status_code in (400, 403)


def test_player_dislike_is_private_and_guest_is_read_only(base_config):
    app = _app(base_config, run_password="runner99")
    db = app.extensions["state"].db
    _seed(base_config["db_path"], base_config["music_dir"], [("song", 120.0, 0, 0)])
    path = f"{base_config['music_dir']}/song.mp3"
    db.add_player("runner", generate_password_hash("runrunrun"))

    player = app.test_client()
    pcsrf = _login(player, username="runner", password="runrunrun")
    r = player.post("/api/track/dislike", json={"path": path, "disliked": True}, headers=pcsrf)
    assert r.status_code == 200 and r.get_json()["disliked"] is True
    r = player.post("/api/track/star", json={"path": path, "starred": True}, headers=pcsrf)
    assert r.get_json()["rating"] == 4

    admin = app.test_client()
    _login(admin, password="s3cret")
    track = admin.get(f"/api/track?path={quote(path)}").get_json()["track"]
    assert track["disliked"] is False and track["rating"] is None

    guest = app.test_client()
    gcsrf = _login(guest, password="runner99")
    for url, body in (("/api/track/rating", {"rating": 5}), ("/api/track/dislike", {"disliked": True}),
                      ("/api/track/star", {"starred": True})):
        assert guest.post(url, json={"path": path, **body}, headers=gcsrf).status_code == 403
