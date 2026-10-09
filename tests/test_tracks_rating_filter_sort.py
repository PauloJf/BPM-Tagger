"""Tracks-page rating filter (unrated / rated1..rated5) + rating sort
(docs/plans/ratings-weighted-picking.md D19), both against the admin's own
rating — db/tracks.py's _tracks_filter/_tracks_order, shared by the paged
listing, the plain path list (Play All / Shuffle) and /api/tracks."""

import pytest

from bpm_tagger.db import BPMDatabase


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _seed(db, name, rating=None):
    path = f"/music/{name}.mp3"
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO tracks (file_path, title, status) VALUES (?, ?, 'done')",
            (path, name))
        conn.commit()
    if rating is not None:
        db.set_rating("admin", path, rating)
    return path


# ── filter ───────────────────────────────────────────────────────────────────

def test_unrated_filter(db):
    _seed(db, "a", 3)
    _seed(db, "b", None)
    rows, total = db.get_tracks_page("", 50, 0, filter="unrated")
    assert total == 1 and rows[0]["title"] == "b"


def test_rated_n_filter_is_at_least_n(db):
    _seed(db, "five", 5)
    _seed(db, "three", 3)
    _seed(db, "one", 1)
    rows, total = db.get_tracks_page("", 50, 0, filter="rated3")
    assert total == 2
    assert {r["title"] for r in rows} == {"five", "three"}


def test_rating_column_present_on_listing_rows(db):
    _seed(db, "a", 4)
    _seed(db, "b", None)
    rows, _ = db.get_tracks_page("", 50, 0)
    by_title = {r["title"]: r["rating"] for r in rows}
    assert by_title == {"a": 4, "b": None}


# ── sort ─────────────────────────────────────────────────────────────────────

def test_rating_sort_descending_unrated_last(db):
    _seed(db, "three", 3)
    _seed(db, "five", 5)
    _seed(db, "none", None)
    rows, _ = db.get_tracks_page("", 50, 0, sort="rating")
    assert [r["title"] for r in rows] == ["five", "three", "none"]


def test_rating_sort_ascending_unrated_last(db):
    _seed(db, "three", 3)
    _seed(db, "one", 1)
    _seed(db, "none", None)
    rows, _ = db.get_tracks_page("", 50, 0, sort="rating_asc")
    assert [r["title"] for r in rows] == ["one", "three", "none"]


def test_path_list_honours_the_same_filter_and_sort(db):
    """get_track_paths (Play All / Shuffle) shares _tracks_filter/_tracks_order —
    it must agree with the paged listing on both the set and the order."""
    _seed(db, "three", 3)
    _seed(db, "five", 5)
    _seed(db, "none", None)
    paths = db.get_track_paths(sort="rating")
    assert [p["title"] for p in paths] == ["five", "three", "none"]
    only_rated = db.get_track_paths(filter="rated5")
    assert [p["title"] for p in only_rated] == ["five"]


# ── API surface ──────────────────────────────────────────────────────────────

def _login(client):
    r = client.post("/api/login", json={"password": "s3cret"})
    assert r.status_code == 200
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def test_api_tracks_rating_filter_and_sort(client, base_config):
    csrf = _login(client)
    import sqlite3
    conn = sqlite3.connect(base_config["db_path"])
    for name in ("five", "three", "none"):
        conn.execute("INSERT INTO tracks (file_path, title, status) VALUES (?, ?, 'done')",
                     (f"{base_config['music_dir']}/{name}.mp3", name))
    conn.commit()
    conn.close()
    for name, rating in (("five", 5), ("three", 3)):
        r = client.post("/api/track/rating",
                        json={"path": f"{base_config['music_dir']}/{name}.mp3", "rating": rating},
                        headers=csrf)
        assert r.status_code == 200

    data = client.get("/api/tracks?sort=rating").get_json()
    assert [t["title"] for t in data["tracks"]] == ["five", "three", "none"]
    assert [t["rating"] for t in data["tracks"]] == [5, 3, None]

    data = client.get("/api/tracks?filter=unrated").get_json()
    assert data["total"] == 1 and data["tracks"][0]["title"] == "none"

    data = client.get("/api/tracks?filter=rated4").get_json()
    assert data["total"] == 1 and data["tracks"][0]["title"] == "five"
