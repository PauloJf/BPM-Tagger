"""Playlist operations — diff, merge, split (all local-first).

Three set operations that read any playlist and only ever write Local ones. What
these pin down:

* the **identity chain** (matched file → ISRC → normalized artist+title) that
  decides whether two rows are the same recording — including the case the chain
  exists for, the same song held as two different files;
* the diff's three buckets, and that its saveable paths are the library-backed
  ones only;
* merge's cross-source dedupe and its four-way report (added / already there /
  duplicate / not in library);
* that a **cadence split agrees with the run rule**, asserted against the very
  numbers /api/run/readiness and the stats strip report rather than against a
  restatement of the maths;
* the artist split's group threshold, and the 404/400 guards on all three.
"""

import os
import sqlite3

import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.web.api.playlist_ops import cluster_rows, identity_keys


def _login(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _seed(base_config, name, *, title=None, artist="Artist", album_artist=None,
          bpm=150.0, isrc=None, norm_title=None, norm_artist=None, status="done",
          duration_ms=200_000):
    """Insert a library track the way the scanner would, and return its path."""
    path = os.path.join(base_config["music_dir"], f"{name}.mp3")
    title = title if title is not None else name
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, album, album_artist, bpm, isrc, "
        "duration_ms, norm_title, norm_artist, status, analyzed_at) "
        "VALUES (?, ?, ?, 'Alb', ?, ?, ?, ?, ?, ?, ?, '2026-01-01')",
        (path, title, artist, album_artist if album_artist is not None else artist,
         bpm, isrc, duration_ms,
         norm_title if norm_title is not None else title.lower(),
         norm_artist if norm_artist is not None else artist.lower(), status))
    conn.commit()
    conn.close()
    return path


def _db(base_config):
    return BPMDatabase(base_config["db_path"])


def _client_with(base_config, **over):
    """A logged-out client on the same DB, with config overrides (run presets)."""
    from bpm_tagger.web.app import create_app

    cfg = dict(base_config)
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


def _unmatched_row(db, pid, *, source_id, title, artist="Artist", isrc=None,
                   norm_title=None, norm_artist=None, position=90):
    """A 'missing' playlist row — a source track with no file on disk."""
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, source_track_id, position, title, "
            "artist, isrc, norm_title, norm_artist, match_status, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'missing', '2026-02-01')",
            (pid, source_id, position, title, artist, isrc,
             norm_title if norm_title is not None else title.lower(),
             norm_artist if norm_artist is not None else artist.lower()))
        conn.commit()


# ── the identity chain ───────────────────────────────────────────────────────

def test_identity_keys_are_ordered_strongest_first():
    keys = identity_keys({"file_path": "/music/a.mp3", "isrc": "gbaaa1200001",
                          "norm_title": "song", "norm_artist": "artist"})
    assert keys == ["f:/music/a.mp3", "i:GBAAA1200001", "n:artist|song"]


def test_identity_falls_back_to_normalizing_untagged_rows():
    """A row with no stored norm_* columns is normalized on the spot, with the
    same functions that wrote them — so it lands on the identical key."""
    stored = identity_keys({"norm_title": "song", "norm_artist": "artist"})
    computed = identity_keys({"title": "Song (2011 Remaster)", "artist": "Artist"})
    assert stored == computed == ["n:artist|song"]


def test_a_row_with_no_identity_at_all_gets_no_keys():
    assert identity_keys({"title": "", "artist": ""}) == []


def test_only_a_have_row_contributes_a_file_identity():
    """matched_file_path alone can be stale; the join's local_file_path is the
    proof the library row is still there."""
    assert identity_keys({"match_status": "have", "local_file_path": "/m/a.mp3",
                          "title": "s"})[0] == "f:/m/a.mp3"
    assert identity_keys({"match_status": "missing", "matched_file_path": "/m/a.mp3",
                          "title": "s"}) == ["n:|s"]


def test_clustering_matches_the_same_file():
    rows = [{"file_path": "/m/a.mp3", "title": "A"}, {"file_path": "/m/a.mp3", "title": "A"}]
    assert len(cluster_rows(rows)) == 1


def test_clustering_matches_two_different_files_sharing_an_isrc():
    """The case the chain exists for: the same recording held as an .mp3 in one
    playlist and an .m4a in the other."""
    rows = [{"file_path": "/m/a.mp3", "isrc": "GBAAA1200001", "norm_title": "x", "norm_artist": "y"},
            {"file_path": "/m/a.m4a", "isrc": "gbaaa1200001", "norm_title": "z", "norm_artist": "w"}]
    assert len(cluster_rows(rows)) == 1


def test_clustering_matches_on_normalized_artist_and_title():
    rows = [{"file_path": "/m/a.mp3", "title": "Song", "artist": "The Band"},
            {"title": "song (Remastered)", "artist": "The Band"}]
    assert len(cluster_rows(rows)) == 1


def test_clustering_keeps_genuinely_different_tracks_apart():
    rows = [{"file_path": "/m/a.mp3", "title": "Alpha", "artist": "Ann", "isrc": "AAA"},
            {"file_path": "/m/b.mp3", "title": "Beta", "artist": "Bo", "isrc": "BBB"}]
    assert len(cluster_rows(rows)) == 2


def test_keyless_rows_do_not_all_collapse_into_one_cluster():
    assert len(cluster_rows([{"title": ""}, {"title": ""}])) == 2


# ── diff ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def pair(client, base_config):
    """Two playlists sharing one track by file, one by ISRC (different files) and
    one by normalized tags (one side unmatched), plus one exclusive each."""
    csrf = _login(client)
    db = _db(base_config)
    a = db.add_local_playlist("A side")
    b = db.add_local_playlist("B side")

    shared = _seed(base_config, "shared", bpm=150.0)
    db.add_track_to_local_playlist(a, shared)
    db.add_track_to_local_playlist(b, shared)

    # Same recording, two files — matched only by ISRC.
    db.add_track_to_local_playlist(a, _seed(base_config, "twin_mp3", title="Twin",
                                            isrc="GBAAA1200001", bpm=160.0))
    db.add_track_to_local_playlist(b, _seed(base_config, "twin_m4a", title="Twin Remaster",
                                            isrc="GBAAA1200001", bpm=160.0,
                                            norm_title="twin remaster"))

    # In A as a library file, in B only as a source row — matched by norm tags.
    db.add_track_to_local_playlist(a, _seed(base_config, "tags", title="Tagged", bpm=170.0))
    _unmatched_row(db, b, source_id="sp-tagged", title="Tagged")

    db.add_track_to_local_playlist(a, _seed(base_config, "onlya", title="OnlyA", bpm=140.0))
    _unmatched_row(db, b, source_id="sp-onlyb", title="OnlyB", position=91)
    return a, b, csrf


def _diff(client, a, b):
    r = client.get(f"/api/playlists/diff?a={a}&b={b}")
    assert r.status_code == 200
    return r.get_json()


def test_diff_buckets_split_the_two_playlists(client, pair):
    a, b, _ = pair
    d = _diff(client, a, b)
    assert d["counts"] == {"both": 3, "only_a": 1, "only_b": 1}
    assert d["only_a"][0]["title"] == "OnlyA"
    assert d["only_b"][0]["title"] == "OnlyB"


def test_diff_pairs_the_same_song_held_as_two_different_files(client, pair):
    a, b, _ = pair
    d = _diff(client, a, b)
    twin = next(e for e in d["both"] if e["a"]["title"] == "Twin")
    assert twin["same_file"] is False
    assert twin["b"]["title"] == "Twin Remaster"
    # …while the row both playlists hold as one file says so.
    same = next(e for e in d["both"] if e["a"]["title"] == "shared")
    assert same["same_file"] is True


def test_diff_matches_an_unmatched_row_by_its_normalized_tags(client, pair):
    a, b, _ = pair
    d = _diff(client, a, b)
    tagged = next(e for e in d["both"] if e["a"]["title"] == "Tagged")
    assert tagged["a"]["matched"] is True and tagged["b"]["matched"] is False
    assert tagged["b"]["path"] is None and tagged["b"]["status"] == "missing"


def test_diff_saveable_paths_are_the_library_backed_rows_only(client, pair):
    a, b, _ = pair
    d = _diff(client, a, b)
    # 'both' saves the A side's files; only_b's single row has no file at all.
    assert len(d["paths"]["both"]) == 3
    assert [os.path.basename(p) for p in d["paths"]["only_a"]] == ["onlya.mp3"]
    assert d["paths"]["only_b"] == []


def test_diff_rows_carry_the_library_track_info(client, pair):
    a, b, _ = pair
    d = _diff(client, a, b)
    row = d["only_a"][0]
    assert row["bpm"] == 140.0 and row["artist"] == "Artist" and row["duration_ms"] == 200_000


def test_diff_ignores_tombstoned_rows(client, base_config):
    _login(client)
    db = _db(base_config)
    a, b = db.add_local_playlist("A"), db.add_local_playlist("B")
    db.add_track_to_local_playlist(a, _seed(base_config, "ghost"))
    with db._connect() as conn:
        conn.execute("UPDATE playlist_tracks SET removed_at = '2026-03-01' WHERE playlist_id = ?", (a,))
        conn.commit()
    assert _diff(client, a, b)["counts"] == {"both": 0, "only_a": 0, "only_b": 0}


def test_diff_404s_for_an_unknown_playlist(client, pair):
    a, _b, _ = pair
    assert client.get(f"/api/playlists/diff?a={a}&b=9999").status_code == 404
    assert client.get(f"/api/playlists/diff?a=9999&b={a}").status_code == 404


def test_diff_requires_two_distinct_ids(client, pair):
    a, _b, _ = pair
    assert client.get("/api/playlists/diff").status_code == 400
    assert client.get(f"/api/playlists/diff?a={a}").status_code == 400
    assert client.get(f"/api/playlists/diff?a={a}&b={a}").status_code == 400
    assert client.get(f"/api/playlists/diff?a={a}&b=nope").status_code == 400


# ── merge ────────────────────────────────────────────────────────────────────

def test_merge_unions_sources_into_a_new_local_playlist(client, base_config):
    csrf = _login(client)
    db = _db(base_config)
    a, b = db.add_local_playlist("A"), db.add_local_playlist("B")
    db.add_track_to_local_playlist(a, _seed(base_config, "one", title="One"))
    db.add_track_to_local_playlist(b, _seed(base_config, "two", title="Two"))

    r = client.post("/api/playlists/merge",
                    json={"source_ids": [a, b], "target": {"name": "Union"}}, headers=csrf)
    assert r.status_code == 200
    body = r.get_json()
    assert body["totals"]["added"] == 2
    target = db.get_playlist(body["target"]["id"])
    assert target["source"] == "local" and target["name"] == "Union"
    assert {t["title"] for t in db.get_playlist_tracks(target["id"])} == {"One", "Two"}


def test_merge_dedupes_across_sources_by_the_identity_chain(client, base_config):
    """The same recording in two sources — once as the identical file, once as a
    different file sharing an ISRC — is added once and reported as a duplicate."""
    csrf = _login(client)
    db = _db(base_config)
    a, b = db.add_local_playlist("A"), db.add_local_playlist("B")
    shared = _seed(base_config, "shared", title="Shared")
    db.add_track_to_local_playlist(a, shared)
    db.add_track_to_local_playlist(b, shared)
    db.add_track_to_local_playlist(a, _seed(base_config, "twin1", title="Twin",
                                            isrc="GBAAA1200001"))
    db.add_track_to_local_playlist(b, _seed(base_config, "twin2", title="Twin Alt",
                                            isrc="GBAAA1200001", norm_title="twin alt"))

    body = client.post("/api/playlists/merge",
                       json={"source_ids": [a, b], "target": {"name": "U"}},
                       headers=csrf).get_json()
    by_id = {s["id"]: s for s in body["sources"]}
    assert by_id[a]["added"] == 2
    assert by_id[b]["added"] == 0 and by_id[b]["skipped_duplicate"] == 2
    assert body["totals"] == {"added": 2, "already_present": 0,
                              "skipped_duplicate": 2, "not_in_library": 0}


def test_merge_reports_rows_with_no_file_on_disk(client, base_config):
    csrf = _login(client)
    db = _db(base_config)
    a = db.add_local_playlist("A")
    db.add_track_to_local_playlist(a, _seed(base_config, "have", title="Have"))
    _unmatched_row(db, a, source_id="sp-1", title="Nowhere")

    body = client.post("/api/playlists/merge",
                       json={"source_ids": [a], "target": {"name": "U"}},
                       headers=csrf).get_json()
    assert body["totals"]["added"] == 1 and body["totals"]["not_in_library"] == 1


def test_merge_into_an_existing_target_is_idempotent(client, base_config):
    csrf = _login(client)
    db = _db(base_config)
    a = db.add_local_playlist("A")
    dest = db.add_local_playlist("Dest")
    db.add_track_to_local_playlist(a, _seed(base_config, "x", title="X"))

    first = client.post("/api/playlists/merge",
                        json={"source_ids": [a], "target": {"id": dest}},
                        headers=csrf).get_json()
    second = client.post("/api/playlists/merge",
                         json={"source_ids": [a], "target": {"id": dest}},
                         headers=csrf).get_json()
    assert first["totals"]["added"] == 1
    assert second["totals"] == {"added": 0, "already_present": 1,
                                "skipped_duplicate": 0, "not_in_library": 0}
    assert len(db.get_playlist_tracks(dest)) == 1


def test_a_big_merge_is_chunked_rather_than_refused(client, base_config, monkeypatch):
    """A merge bigger than one bulk-add request must still go through — in
    bounded transactions, so it never holds the write lock for the whole lot."""
    csrf = _login(client)
    db = _db(base_config)
    total = 620
    paths = []
    conn = sqlite3.connect(base_config["db_path"])
    for i in range(total):
        path = os.path.join(base_config["music_dir"], f"big{i}.mp3")
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, bpm, status, analyzed_at) "
            "VALUES (?, ?, 'Artist', 150.0, 'done', '2026-01-01')", (path, f"Big {i}"))
        paths.append(path)
    conn.commit()
    conn.close()
    a = db.add_local_playlist("Big")
    db.add_tracks_to_local_playlist(a, paths)

    sizes = []
    real = BPMDatabase.add_tracks_to_local_playlist

    def counted(self, playlist_id, file_paths):
        sizes.append(len(file_paths))
        return real(self, playlist_id, file_paths)

    monkeypatch.setattr(BPMDatabase, "add_tracks_to_local_playlist", counted)
    body = client.post("/api/playlists/merge",
                       json={"source_ids": [a], "target": {"name": "Union"}},
                       headers=csrf).get_json()

    assert body["totals"] == {"added": total, "already_present": 0,
                              "skipped_duplicate": 0, "not_in_library": 0}
    assert body["sources"][0]["added"] == total
    assert sum(sizes) == total and max(sizes) <= 500 and len(sizes) > 1
    assert len(db.get_playlist_tracks(body["target"]["id"])) == total


def test_merge_refuses_a_non_local_target(client, base_config):
    csrf = _login(client)
    db = _db(base_config)
    a = db.add_local_playlist("A")
    spotify = db.add_playlist("sp-1", "Spotify mirror")
    r = client.post("/api/playlists/merge",
                    json={"source_ids": [a], "target": {"id": spotify}}, headers=csrf)
    assert r.status_code == 400
    assert "local" in r.get_json()["error"]


def test_merge_reads_any_source_type(client, base_config):
    """A Spotify mirror is a perfectly good *source* — only the target is local."""
    csrf = _login(client)
    db = _db(base_config)
    spotify = db.add_playlist("sp-2", "Mirror")
    path = _seed(base_config, "mirrored", title="Mirrored")
    db.sync_playlist_tracks(spotify, [{"source_track_id": "t1", "title": "Mirrored",
                                       "match_status": "have", "matched_file_path": path}])
    body = client.post("/api/playlists/merge",
                       json={"source_ids": [spotify], "target": {"name": "From Spotify"}},
                       headers=csrf).get_json()
    assert body["totals"]["added"] == 1


def test_merge_404s_for_an_unknown_source(client, base_config):
    csrf = _login(client)
    a = _db(base_config).add_local_playlist("A")
    r = client.post("/api/playlists/merge",
                    json={"source_ids": [a, 9999], "target": {"name": "U"}}, headers=csrf)
    assert r.status_code == 404


def test_merge_404s_for_an_unknown_target(client, base_config):
    csrf = _login(client)
    a = _db(base_config).add_local_playlist("A")
    r = client.post("/api/playlists/merge",
                    json={"source_ids": [a], "target": {"id": 9999}}, headers=csrf)
    assert r.status_code == 404


def test_merge_rejects_a_missing_or_malformed_body(client, base_config):
    csrf = _login(client)
    a = _db(base_config).add_local_playlist("A")
    assert client.post("/api/playlists/merge", json={"target": {"name": "U"}},
                       headers=csrf).status_code == 400
    assert client.post("/api/playlists/merge", json={"source_ids": [], "target": {"name": "U"}},
                       headers=csrf).status_code == 400
    assert client.post("/api/playlists/merge", json={"source_ids": ["x"], "target": {"name": "U"}},
                       headers=csrf).status_code == 400
    assert client.post("/api/playlists/merge", json={"source_ids": [a]},
                       headers=csrf).status_code == 400
    assert client.post("/api/playlists/merge", json={"source_ids": [a], "target": {"name": " "}},
                       headers=csrf).status_code == 400


def test_merge_requires_the_csrf_token(client, base_config):
    _login(client)
    a = _db(base_config).add_local_playlist("A")
    r = client.post("/api/playlists/merge", json={"source_ids": [a], "target": {"name": "U"}})
    assert r.status_code in (400, 403)


# ── split: cadence ───────────────────────────────────────────────────────────

@pytest.fixture
def runnable(client, base_config):
    """A playlist with one track squarely on each of the first two presets and
    one that reaches neither."""
    csrf = _login(client)
    db = _db(base_config)
    pid = db.add_local_playlist("Long Run")
    db.add_track_to_local_playlist(pid, _seed(base_config, "easy", title="Easy", bpm=155.0))
    db.add_track_to_local_playlist(pid, _seed(base_config, "fast", title="Fast", bpm=175.0))
    db.add_track_to_local_playlist(pid, _seed(base_config, "slow", title="Slow", bpm=95.0))
    return pid, csrf


def test_cadence_split_agrees_with_the_run_rule(client, runnable):
    """The split's groups must hold exactly as many tracks as the run readiness
    view says are runnable there — both go through _eligible over the same
    candidate pool, and this is what proves they can't drift."""
    pid, csrf = runnable
    stats = client.get(f"/api/playlists/{pid}/stats").get_json()
    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                       headers=csrf).get_json()

    by_group = {p["group"]: p for p in body["playlists"]}
    empty = {s["group"] for s in body["skipped"]}
    for preset in stats["presets"]:
        expected = stats["runnable"][str(preset["bpm"])]
        if expected:
            assert by_group[preset["name"]]["eligible"] == expected
            assert by_group[preset["name"]]["added"] == expected
        else:
            assert preset["name"] in empty


def test_cadence_split_writes_local_playlists_named_after_the_preset(client, runnable):
    pid, csrf = runnable
    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                       headers=csrf).get_json()
    assert body["playlists"], "at least one preset should have matched"
    for out in body["playlists"]:
        assert out["name"] == f"Long Run · {out['group']}"
        assert out["created"] is True


def test_cadence_split_tops_up_instead_of_duplicating_on_a_rerun(client, runnable, base_config):
    pid, csrf = runnable
    first = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                        headers=csrf).get_json()
    second = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                         headers=csrf).get_json()
    assert [p["id"] for p in first["playlists"]] == [p["id"] for p in second["playlists"]]
    assert all(p["created"] is False and p["added"] == 0 for p in second["playlists"])
    assert all(p["already_present"] > 0 for p in second["playlists"])
    # No second copy of any output playlist got created.
    names = [p["name"] for p in _db(base_config).list_playlists()]
    assert len(names) == len(set(names))


def test_a_long_source_name_is_trimmed_without_losing_the_group(client, base_config):
    """Two presets of an over-long playlist name must not truncate onto the same
    output name and silently merge — the source half is what gives."""
    csrf = _login(client)
    db = _db(base_config)
    pid = db.add_local_playlist("L" * 240)
    db.add_track_to_local_playlist(pid, _seed(base_config, "mid", bpm=160.0))
    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                       headers=csrf).get_json()
    names = [p["name"] for p in body["playlists"]]
    assert len(names) == len(set(names)) >= 2
    for out in body["playlists"]:
        assert len(out["name"]) <= 200
        assert out["name"].endswith(f" · {out['group']}")


def test_a_track_may_land_in_several_cadence_groups(client, base_config):
    """At ±15% a 158 BPM track reaches both 155 and 165 — the split must say so
    rather than assigning it to one."""
    csrf = _login(client)
    db = _db(base_config)
    pid = db.add_local_playlist("Overlap")
    db.add_track_to_local_playlist(pid, _seed(base_config, "mid", title="Mid", bpm=160.0))
    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                       headers=csrf).get_json()
    assert len([p for p in body["playlists"] if p["eligible"] == 1]) >= 2


def test_split_preview_reports_the_same_counts_as_the_stats_strip(client, runnable):
    pid, _ = runnable
    preview = client.get(f"/api/playlists/{pid}/split").get_json()
    stats = client.get(f"/api/playlists/{pid}/stats").get_json()
    assert preview["presets"] == stats["presets"]
    assert preview["cadence"] == [{"group": p["name"], "bpm": p["bpm"],
                                   "count": stats["runnable"][str(p["bpm"])]}
                                  for p in stats["presets"]]


def test_two_presets_of_the_same_name_stay_two_groups(base_config):
    """Nothing stops two run presets being called "Easy". Their groups, their
    output playlists and their preview counts must still be two things — the BPM
    is appended to tell them apart."""
    client = _client_with(base_config, run_presets=[{"name": "Easy", "bpm": 150},
                                                    {"name": "Easy", "bpm": 170}])
    csrf = _login(client)
    db = _db(base_config)
    pid = db.add_local_playlist("Long Run")
    db.add_track_to_local_playlist(pid, _seed(base_config, "a", bpm=150.0))
    db.add_track_to_local_playlist(pid, _seed(base_config, "b", bpm=170.0))

    preview = client.get(f"/api/playlists/{pid}/split").get_json()
    assert [c["group"] for c in preview["cadence"]] == ["Easy 150", "Easy 170"]
    assert [c["bpm"] for c in preview["cadence"]] == [150, 170]

    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                       headers=csrf).get_json()
    groups = [p["group"] for p in body["playlists"]] + [s["group"] for s in body["skipped"]]
    assert sorted(groups) == ["Easy 150", "Easy 170"]
    names = [p["name"] for p in body["playlists"]]
    assert names == [f"Long Run · {p['group']}" for p in body["playlists"]]
    assert len(names) == len(set(names))
    assert len({p["id"] for p in body["playlists"]}) == len(names)


def test_a_unique_preset_name_is_left_alone(base_config):
    """The BPM is appended only to disambiguate — a name that stands on its own
    is what the user typed."""
    client = _client_with(base_config, run_presets=[{"name": "Easy", "bpm": 150},
                                                    {"name": "Tempo", "bpm": 170}])
    csrf = _login(client)
    db = _db(base_config)
    pid = db.add_local_playlist("Long Run")
    db.add_track_to_local_playlist(pid, _seed(base_config, "a", bpm=150.0))

    preview = client.get(f"/api/playlists/{pid}/split").get_json()
    assert [c["group"] for c in preview["cadence"]] == ["Easy", "Tempo"]
    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "cadence"},
                       headers=csrf).get_json()
    assert body["playlists"][0]["name"] == "Long Run · Easy"


# ── split: artist ────────────────────────────────────────────────────────────

@pytest.fixture
def by_artist(client, base_config):
    csrf = _login(client)
    db = _db(base_config)
    pid = db.add_local_playlist("Mixtape")
    for i in range(3):
        db.add_track_to_local_playlist(pid, _seed(base_config, f"big{i}", artist="Big Band"))
    for i in range(2):
        db.add_track_to_local_playlist(pid, _seed(base_config, f"small{i}", artist="Small Act"))
    return pid, csrf


def test_artist_split_only_makes_playlists_above_the_threshold(client, by_artist):
    pid, csrf = by_artist
    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "artist"},
                       headers=csrf).get_json()
    assert [(p["group"], p["added"]) for p in body["playlists"]] == [("Big Band", 3)]
    assert body["playlists"][0]["name"] == "Mixtape · Big Band"
    assert body["skipped"] == [{"group": "Small Act", "count": 2, "reason": "too_small"}]


def test_artist_split_groups_by_album_artist(client, base_config):
    """Two tracks credited to different guests but one album artist stay together
    — playlist split groups by COALESCE(album_artist, artist), independent of
    the Artists browse index (which links every individually credited artist)."""
    csrf = _login(client)
    db = _db(base_config)
    pid = db.add_local_playlist("Comp")
    for i, guest in enumerate(["Guest A", "Guest B", "Guest C"]):
        db.add_track_to_local_playlist(
            pid, _seed(base_config, f"c{i}", artist=guest, album_artist="Various Hosts"))
    body = client.post(f"/api/playlists/{pid}/split", json={"mode": "artist"},
                       headers=csrf).get_json()
    assert [(p["group"], p["added"]) for p in body["playlists"]] == [("Various Hosts", 3)]


def test_artist_split_preview_lists_groups_and_leftovers(client, by_artist):
    pid, _ = by_artist
    preview = client.get(f"/api/playlists/{pid}/split").get_json()["artist"]
    assert preview["groups"] == [{"group": "Big Band", "count": 3}]
    assert preview["skipped"] == [{"group": "Small Act", "count": 2, "reason": "too_small"}]
    assert preview["min_group"] == 3


# ── split guards ─────────────────────────────────────────────────────────────

def test_split_404s_for_an_unknown_playlist(client, base_config):
    csrf = _login(client)
    assert client.post("/api/playlists/9999/split", json={"mode": "cadence"},
                       headers=csrf).status_code == 404
    assert client.get("/api/playlists/9999/split").status_code == 404


def test_split_rejects_an_unknown_mode(client, runnable):
    pid, csrf = runnable
    r = client.post(f"/api/playlists/{pid}/split", json={"mode": "colour"}, headers=csrf)
    assert r.status_code == 400
    assert client.post(f"/api/playlists/{pid}/split", json={}, headers=csrf).status_code == 400


def test_split_accepts_any_source_type(client, base_config):
    """Slicing a Spotify mirror into runnable Local playlists is the point."""
    csrf = _login(client)
    db = _db(base_config)
    spotify = db.add_playlist("sp-3", "Mirror")
    path = _seed(base_config, "m1", title="M1", bpm=155.0)
    db.sync_playlist_tracks(spotify, [{"source_track_id": "t1", "title": "M1",
                                       "match_status": "have", "matched_file_path": path}])
    body = client.post(f"/api/playlists/{spotify}/split", json={"mode": "cadence"},
                       headers=csrf).get_json()
    assert body["playlists"]
    # …and every output is Local, never a write back to Spotify.
    assert all(db.get_playlist(p["id"])["source"] == "local" for p in body["playlists"])


def test_split_requires_the_csrf_token(client, runnable):
    pid, _ = runnable
    assert client.post(f"/api/playlists/{pid}/split",
                       json={"mode": "cadence"}).status_code in (400, 403)


# ── access ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/api/playlists/diff?a=1&b=2"),
    ("post", "/api/playlists/merge"),
    ("post", "/api/playlists/1/split"),
    ("get", "/api/playlists/1/split"),
])
def test_operations_require_a_session(app, method, path):
    r = getattr(app.test_client(), method)(path)
    assert r.status_code in (401, 403)


def test_operations_are_not_in_the_player_allowlist():
    """Playlist management has never been a player capability, and the allowlist
    is default-deny — so these must simply not be named in it."""
    from bpm_tagger.web.app import _PLAYER_ALLOWED
    assert not any(ep.startswith("api_playlist_ops.") for ep in _PLAYER_ALLOWED)
