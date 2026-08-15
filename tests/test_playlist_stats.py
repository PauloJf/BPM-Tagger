"""Playlist stats strip (`GET /api/playlists/<id>/stats`) and the library's
play-count sort.

The strip rolls up a playlist's MATCHED tracks — the ones actually on disk — so
these pin the arithmetic (runtime, 5-BPM buckets, plays) against a hand-built
fixture playlist, check that the per-preset runnable counts are literally the
same numbers /api/run/readiness reports (both call preset_counts), and cover the
listing sort the Plays column drives.
"""

import os
import sqlite3

import pytest

from bpm_tagger.db import BPMDatabase


def _login(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _seed(base_config, name, *, bpm=None, duration_ms=None, play_count=0,
          status="done", disliked=0):
    """Insert a library track as the scanner would and return its path."""
    path = os.path.join(base_config["music_dir"], f"{name}.mp3")
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, bpm, duration_ms, play_count, "
        "status, disliked, analyzed_at) VALUES (?, ?, 'Artist', ?, ?, ?, ?, ?, ?)",
        (path, name, bpm, duration_ms, play_count, status, disliked, f"2026-01-{name[-1]}"))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def playlist(client, base_config):
    """A Local playlist with three matched tracks (one of them un-analyzed), one
    row whose library file has since been deleted, and one unmatched row — so
    every exclusion the rollup has to make is exercised at once."""
    csrf = _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("Long Run")

    for path in (_seed(base_config, "alpha", bpm=122.0, duration_ms=180_000, play_count=30),
                 _seed(base_config, "beta", bpm=157.0, duration_ms=240_000, play_count=12),
                 _seed(base_config, "gamma", bpm=None, duration_ms=300_000, play_count=5)):
        db.add_track_to_local_playlist(pid, path)

    gone = _seed(base_config, "delta", bpm=140.0, duration_ms=999_000, play_count=99,
                 status="deleted")
    with db._connect() as conn:
        # A 'have' row pointing at a library file that has since been deleted —
        # the join must drop it (add_track_to_local_playlist refuses to make one).
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, source_track_id, position, title, "
            "duration_ms, match_status, matched_file_path, first_seen_at) "
            "VALUES (?, ?, 8, 'delta', 999000, 'have', ?, '2026-02-01')",
            (pid, gone, gone))
        # An unmatched row (a source track with no local file) — 'missing', so it
        # counts toward coverage but never toward the on-disk rollup.
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, source_track_id, position, title, "
            "artist, duration_ms, match_status, first_seen_at) "
            "VALUES (?, 'src-x', 9, 'Nowhere', 'Artist', 500000, 'missing', '2026-02-01')",
            (pid,))
        conn.commit()
    return pid, csrf


# ── the rollup ───────────────────────────────────────────────────────────────

def test_stats_counts_only_matched_on_disk_tracks(client, playlist):
    pid, _ = playlist
    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    # alpha + beta + gamma. The deleted library file and the unmatched row are out.
    assert m["count"] == 3
    assert m["analyzed"] == 2                     # gamma has no BPM yet


def test_stats_runtime_sums_matched_durations(client, playlist):
    pid, _ = playlist
    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    assert m["runtime_ms"] == 180_000 + 240_000 + 300_000


def test_stats_buckets_bpm_five_wide_like_the_stats_page(client, playlist):
    pid, _ = playlist
    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    # 122 → the 120 bucket, 157 → the 155 bucket; gamma isn't bucketed at all.
    assert m["bpm_distribution"] == [{"bpm": 120, "count": 1}, {"bpm": 155, "count": 1}]


def test_stats_plays_total_and_top_three(client, playlist):
    pid, _ = playlist
    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    assert m["plays_total"] == 30 + 12 + 5        # the deleted file's 99 is excluded
    assert [(t["title"], t["play_count"]) for t in m["top_played"]] == [
        ("alpha", 30), ("beta", 12), ("gamma", 5)]


def test_stats_top_played_is_capped_at_three_and_skips_the_unplayed(client, base_config):
    csrf = _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("Big")
    for i, plays in enumerate([5, 4, 3, 2, 0]):
        db.add_track_to_local_playlist(
            pid, _seed(base_config, f"t{i}", bpm=150.0, duration_ms=1000, play_count=plays))
    _ = csrf

    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    assert [t["play_count"] for t in m["top_played"]] == [5, 4, 3]


def test_stats_counts_a_doubly_listed_file_once(client, base_config):
    """Two source rows can resolve to one library file; runtime and plays must not
    double-count it (the same dedupe the run-candidate query does)."""
    _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("Dupes")
    path = _seed(base_config, "solo", bpm=150.0, duration_ms=200_000, play_count=7)
    db.add_track_to_local_playlist(pid, path)
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, source_track_id, position, title, "
            "duration_ms, match_status, matched_file_path, first_seen_at) "
            "VALUES (?, 'other-id', 1, 'solo again', 200000, 'have', ?, '2026-02-01')",
            (pid, path))
        conn.commit()

    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    assert m["count"] == 1
    assert m["runtime_ms"] == 200_000 and m["plays_total"] == 7


def test_stats_ignores_tombstoned_rows(client, base_config):
    _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("Tombs")
    path = _seed(base_config, "ghost", bpm=150.0, duration_ms=200_000, play_count=4)
    db.add_track_to_local_playlist(pid, path)
    with db._connect() as conn:
        conn.execute("UPDATE playlist_tracks SET removed_at = '2026-03-01' "
                     "WHERE playlist_id = ?", (pid,))
        conn.commit()

    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    assert m["count"] == 0 and m["runtime_ms"] == 0 and m["plays_total"] == 0


def test_stats_falls_back_to_the_source_rows_duration(client, base_config):
    """A library indexed before duration tagging still totals something sane."""
    _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("NoDur")
    path = _seed(base_config, "untimed", bpm=150.0, duration_ms=None)
    db.add_track_to_local_playlist(pid, path)
    with db._connect() as conn:
        conn.execute("UPDATE playlist_tracks SET duration_ms = 123456 WHERE playlist_id = ?",
                     (pid,))
        conn.commit()

    m = client.get(f"/api/playlists/{pid}/stats").get_json()["matched"]
    assert m["runtime_ms"] == 123456


# ── runnable counts + staleness ──────────────────────────────────────────────

def test_runnable_counts_match_the_playlist_cards(client, playlist):
    """The strip and the cards' quiet badges must never disagree — both go
    through preset_counts(), and this is what proves it."""
    pid, _ = playlist
    stats = client.get(f"/api/playlists/{pid}/stats").get_json()
    readiness = client.get("/api/run/readiness").get_json()
    card = next(p for p in readiness["playlists"] if p["id"] == pid)

    assert stats["presets"] == readiness["presets"]
    assert stats["runnable"] == card["counts"]
    assert stats["stretch_limit_pct"] == readiness["stretch_limit_pct"]
    # beta (157) reaches the 155 preset inside the default ±15%; alpha (122) can't,
    # and the un-analyzed track is never a run candidate.
    assert stats["runnable"]["155"] == 1


def test_runnable_excludes_disliked_and_unanalyzed(client, base_config):
    _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("Picky")
    for name, bpm, dis in [("keep", 155.0, 0), ("nope", 155.0, 1), ("raw", None, 0)]:
        db.add_track_to_local_playlist(
            pid, _seed(base_config, name, bpm=bpm, duration_ms=1000, disliked=dis))

    stats = client.get(f"/api/playlists/{pid}/stats").get_json()
    assert stats["runnable"]["155"] == 1
    # …while the on-disk rollup keeps all three: it answers "what's here", not
    # "what's runnable".
    assert stats["matched"]["count"] == 3


def test_stats_reports_the_last_membership_change(client, playlist):
    pid, _ = playlist
    stats = client.get(f"/api/playlists/{pid}/stats").get_json()
    assert stats["source"] == "local"
    # Local adds stamp first_seen_at, so the change is dated even though a local
    # playlist never syncs.
    assert stats["last_change_at"] is not None
    assert stats["last_synced_at"] is None


def test_last_change_prefers_a_tombstone_over_an_older_add(client, base_config):
    _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("Diffed")
    db.add_track_to_local_playlist(pid, _seed(base_config, "one", bpm=150.0))
    with db._connect() as conn:
        conn.execute("UPDATE playlist_tracks SET first_seen_at = '2026-01-01T00:00:00', "
                     "removed_at = '2026-04-01T00:00:00' WHERE playlist_id = ?", (pid,))
        conn.commit()

    assert db.get_playlist_last_change(pid) == "2026-04-01T00:00:00"


def test_last_change_is_none_for_an_empty_playlist(client, base_config):
    _login(client)
    db = BPMDatabase(base_config["db_path"])
    pid = db.add_local_playlist("Empty")
    stats = client.get(f"/api/playlists/{pid}/stats").get_json()
    assert stats["last_change_at"] is None
    assert stats["matched"]["count"] == 0


# ── access + errors ──────────────────────────────────────────────────────────

def test_stats_404s_for_an_unknown_playlist(client):
    _login(client)
    assert client.get("/api/playlists/9999/stats").status_code == 404


def test_stats_requires_a_session(app, client, playlist):
    pid, _ = playlist
    # A second client on the same app, with no session cookie of its own.
    r = app.test_client().get(f"/api/playlists/{pid}/stats")
    assert r.status_code in (401, 403)


# ── library play-count sort ──────────────────────────────────────────────────

@pytest.fixture
def library(client, base_config):
    _login(client)
    _seed(base_config, "low1", bpm=150.0, play_count=1)
    _seed(base_config, "high3", bpm=150.0, play_count=30)
    _seed(base_config, "mid2", bpm=150.0, play_count=7)
    _seed(base_config, "none4", bpm=150.0, play_count=0)
    return base_config


def _titles(resp):
    return [t["title"] for t in resp.get_json()["tracks"]]


def test_tracks_default_order_is_unchanged(client, library):
    r = client.get("/api/tracks")
    # analyzed_at DESC, exactly as before — the sort must be opt-in.
    assert _titles(r) == ["none4", "high3", "mid2", "low1"]
    assert r.get_json()["sort"] == ""


def test_tracks_sort_by_plays_descending(client, library):
    r = client.get("/api/tracks?sort=plays")
    assert _titles(r) == ["high3", "mid2", "low1", "none4"]
    assert r.get_json()["sort"] == "plays"


def test_tracks_sort_by_plays_ascending(client, library):
    assert _titles(client.get("/api/tracks?sort=plays_asc")) == \
        ["none4", "low1", "mid2", "high3"]


def test_tracks_rows_carry_the_play_count(client, library):
    rows = {t["title"]: t for t in client.get("/api/tracks").get_json()["tracks"]}
    assert rows["high3"]["play_count"] == 30
    assert rows["none4"]["play_count"] == 0


def test_unknown_sort_falls_back_to_the_default(client, library):
    r = client.get("/api/tracks?sort=DROP%20TABLE")
    assert _titles(r) == ["none4", "high3", "mid2", "low1"]
    # Echoed back normalized, so the client can tell which order it actually got.
    assert r.get_json()["sort"] == ""


def test_sort_composes_with_a_filter(client, library, base_config):
    _seed(base_config, "star5", bpm=150.0, play_count=99)
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("UPDATE tracks SET starred = 1 WHERE title IN ('star5', 'low1')")
    conn.commit()
    conn.close()

    assert _titles(client.get("/api/tracks?filter=starred&sort=plays")) == ["star5", "low1"]


def test_track_paths_follows_the_same_sort(client, library):
    """Play all must queue what the table shows, sorted order included."""
    rows = client.get("/api/tracks/paths?sort=plays").get_json()["tracks"]
    assert [os.path.basename(t["file_path"]) for t in rows] == [
        "high3.mp3", "mid2.mp3", "low1.mp3", "none4.mp3"]
