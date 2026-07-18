"""Grabber API: spotify status/connect, playlists CRUD, tracks classification,
manual sync — with a fake Spotify client (no network)."""

import pytest

from bpm_tagger.grabber import matching as m
from bpm_tagger.grabber.sync_engine import GrabberService
from bpm_tagger.web.app import create_app


class FakeClient:
    """Stand-in for SpotifyClient — deterministic, no network."""
    def __init__(self, tracks=None):
        self._tracks = tracks or []
        self.connected = True

    def is_configured(self):
        return True

    def is_connected(self):
        return self.connected

    def status(self):
        return {"configured": True, "connected": self.connected, "scope": "x", "redirect_uri": "u"}

    def disconnect(self):
        self.connected = False

    def get_playlist_meta(self, pid):
        return {"spotify_id": pid, "name": f"Playlist {pid}", "snapshot_id": "snap1",
                "image_url": "", "track_count": len(self._tracks), "owner": "me"}

    def get_playlist_tracks(self, pid):
        return list(self._tracks)

    def get_my_playlists(self):
        return [
            {"spotify_id": "PL123", "name": "Running Mix", "image_url": "",
             "track_count": 42, "owner": "me"},
            {"spotify_id": "PL456", "name": "Chill", "image_url": "",
             "track_count": 7, "owner": "me"},
        ]

    def search_tracks(self, query, limit=20):
        return [
            {"spotify_track_id": "sid0", "title": "Blinding Lights", "artist": "The Weeknd",
             "album": "After Hours", "album_artist": "The Weeknd", "duration_ms": 200000, "isrc": "",
             "track_no": 1, "disc_no": 1, "year": 2020, "cover_url": "",
             "norm_title": m.normalize_title("Blinding Lights"), "norm_artist": m.normalize_artist("The Weeknd")},
            {"spotify_track_id": "sidX", "title": "Brand New Track", "artist": "Someone",
             "album": "", "album_artist": "Someone", "duration_ms": 180000, "isrc": "",
             "track_no": 1, "disc_no": 1, "year": 2021, "cover_url": "",
             "norm_title": m.normalize_title("Brand New Track"), "norm_artist": m.normalize_artist("Someone")},
        ]


class _Tagger:
    def __init__(self, db):
        self.db = db
        self.notifier = None
        self.grabber = None

    def index_tags(self):
        return 0


def _sp_track(pos, title, artist, **kw):
    return {"spotify_track_id": f"sid{pos}", "position": pos, "title": title,
            "artist": artist, "album": kw.get("album", ""), "album_artist": artist,
            "duration_ms": kw.get("duration_ms", 200000), "isrc": kw.get("isrc", ""),
            "track_no": pos, "disc_no": 1, "year": 2020, "cover_url": "", "added_at": "",
            "norm_title": m.normalize_title(title), "norm_artist": m.normalize_artist(artist),
            "match_status": "unknown", "matched_file_path": None}


@pytest.fixture
def grab(tmp_path):
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(tmp_path / "music"),
        "ui_password": "s3cret",
        "ui_secret_key": "k",
        "grabber_enabled": True,
        "spotify_client_id": "cid", "spotify_client_secret": "sec",
        "spotify_redirect_uri": "http://localhost:5000/api/spotify/callback",
        "index_tags": True,
    }
    import os
    os.makedirs(config["music_dir"], exist_ok=True)
    app = create_app(config)
    st = app.extensions["state"]
    tagger = _Tagger(st.db)
    # Two playlist tracks: one present in the library, one missing.
    tracks = [_sp_track(0, "Blinding Lights", "The Weeknd", album="After Hours"),
              _sp_track(1, "Totally Missing Song", "Nobody At All")]
    service = GrabberService(config, st.db, tagger, None)
    fake = FakeClient(tracks)
    service.client = fake
    service.sync.client = fake
    tagger.grabber = service
    st.tagger = tagger

    # Seed the library with the first track so it matches as "have".
    from bpm_tagger.bpm.tags import get_file_hash
    lib = tmp_path / "music" / "bl.mp3"
    lib.write_bytes(b"\x00")
    st.db.upsert_track(str(lib), get_file_hash(str(lib)), 171.0, None, None, 171.0, 0.9, "librosa", "done")
    st.db.update_track_tags(str(lib), {
        "title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours",
        "album_artist": "The Weeknd", "track_no": 1, "disc_no": 1, "year": 2020,
        "isrc": "", "duration_ms": 200000,
        "norm_title": m.normalize_title("Blinding Lights"),
        "norm_artist": m.normalize_artist("The Weeknd"),
    }, get_file_hash(str(lib)))

    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st, fake


def test_spotify_status_connected(grab):
    client, _, _ = grab
    body = client.get("/api/spotify/status").get_json()
    assert body["enabled"] is True
    assert body["connected"] is True


def test_grabber_status_disabled_without_service(client):
    # The default conftest app has no grabber attached.
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.get("/api/grabber/status").get_json()["enabled"] is False


def test_add_playlist_requires_csrf(grab):
    client, _, _ = grab
    assert client.post("/api/playlists", json={"url": "abc"}).status_code == 403


def test_add_and_list_playlist(grab):
    client, _, _ = grab
    r = client.post("/api/playlists", json={"url": "https://open.spotify.com/playlist/PL123"},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    assert r.get_json()["playlist"]["spotify_id"] == "PL123"
    listing = client.get("/api/playlists").get_json()["playlists"]
    assert any(p["spotify_id"] == "PL123" for p in listing)


def test_browse_my_playlists_flags_watched(grab):
    client, st, _ = grab
    st.db.add_playlist("PL123", "Running Mix")
    body = client.get("/api/spotify/playlists").get_json()
    by_id = {p["spotify_id"]: p for p in body["playlists"]}
    assert by_id["PL123"]["watched"] is True
    assert by_id["PL456"]["watched"] is False


def test_browse_my_playlists_requires_connection(grab):
    client, _, fake = grab
    fake.connected = False
    assert client.get("/api/spotify/playlists").status_code == 400


def test_sync_classifies_have_and_auto_enqueues_missing(grab):
    client, st, _ = grab
    pid = st.db.add_playlist("PL123", "Playlist PL123")
    r = client.post(f"/api/playlists/{pid}/sync", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200

    have = client.get(f"/api/playlists/{pid}/tracks?status=have").get_json()["tracks"]
    # The missing track is auto-enqueued during sync, so it now classifies as queued.
    queued = client.get(f"/api/playlists/{pid}/tracks?status=queued").get_json()["tracks"]
    missing = client.get(f"/api/playlists/{pid}/tracks?status=missing").get_json()["tracks"]
    assert [t["title"] for t in have] == ["Blinding Lights"]
    assert [t["title"] for t in queued] == ["Totally Missing Song"]
    assert missing == []

    pl = next(p for p in client.get("/api/playlists").get_json()["playlists"] if p["id"] == pid)
    assert pl["have_count"] == 1 and pl["queued_count"] == 1 and pl["missing_count"] == 0
    # And a pending grab-queue item was created for it.
    assert st.db.get_queue_counts().get("pending") == 1


def test_disconnect(grab):
    client, _, fake = grab
    assert client.post("/api/spotify/disconnect", headers={"X-CSRF-Token": client._csrf}).status_code == 200
    assert fake.connected is False


def test_delete_playlist(grab):
    client, st, _ = grab
    pid = st.db.add_playlist("PLX", "X")
    assert client.delete(f"/api/playlists/{pid}", headers={"X-CSRF-Token": client._csrf}).status_code == 200
    assert st.db.get_playlist(pid) is None


def test_queue_list_cancel_retry(grab):
    client, st, _ = grab
    csrf = {"X-CSRF-Token": client._csrf}
    pid = st.db.add_playlist("PL123", "P")
    client.post(f"/api/playlists/{pid}/sync", headers=csrf)  # auto-enqueues the missing track

    q = client.get("/api/queue").get_json()
    assert q["counts"].get("pending") == 1
    iid = q["items"][0]["id"]
    assert q["items"][0]["title"] == "Totally Missing Song"

    # detail carries events
    d = client.get(f"/api/queue/{iid}").get_json()
    assert d["item"]["id"] == iid and "events" in d

    assert client.post(f"/api/queue/{iid}/cancel", headers=csrf).status_code == 200
    assert st.db.get_grab_item(iid)["status"] == "skipped"
    assert client.post(f"/api/queue/{iid}/retry", headers=csrf).status_code == 200
    assert st.db.get_grab_item(iid)["status"] == "pending"


def test_enqueue_missing_requeues_after_cancel(grab):
    client, st, _ = grab
    csrf = {"X-CSRF-Token": client._csrf}
    pid = st.db.add_playlist("PL123", "P")
    client.post(f"/api/playlists/{pid}/sync", headers=csrf)
    iid = client.get("/api/queue").get_json()["items"][0]["id"]
    client.post(f"/api/queue/{iid}/cancel", headers=csrf)  # skipped (terminal)

    # The track is now missing again with no in-flight grab → enqueue-missing re-adds it.
    r = client.post(f"/api/playlists/{pid}/enqueue-missing", headers=csrf)
    assert r.status_code == 200 and r.get_json()["enqueued"] == 1


def _awaiting_item(db):
    iid = db.enqueue_grab({"spotify_track_id": "amb1", "title": "Ambiguous", "artist": "X"})
    db.transition(iid, "awaiting_user", "test")
    db.add_grab_candidates(iid, [{"provider": "fake", "provider_track_id": "c1", "title": "Ambiguous",
                                  "artist": "X", "album": "", "duration_ms": 200000, "isrc": "",
                                  "quality": "LOSSLESS", "score": 0.7, "score_breakdown": "{}",
                                  "url": "", "cover_url": "", "rank": 0}])
    return iid


def test_inbox_lists_awaiting_with_candidates(grab):
    client, st, _ = grab
    _awaiting_item(st.db)
    body = client.get("/api/inbox").get_json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Ambiguous"
    assert len(body["items"][0]["candidates"]) == 1


def test_inbox_choose_sets_pending(grab):
    client, st, _ = grab
    iid = _awaiting_item(st.db)
    cid = st.db.get_grab_candidates(iid)[0]["id"]
    r = client.post(f"/api/inbox/{iid}/choose", json={"candidate_id": cid},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    item = st.db.get_grab_item(iid)
    assert item["status"] == "pending" and item["chosen_candidate_id"] == cid


def test_inbox_search_sets_override(grab):
    client, st, _ = grab
    iid = _awaiting_item(st.db)
    r = client.post(f"/api/inbox/{iid}/search", json={"query": "better query"},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    item = st.db.get_grab_item(iid)
    assert item["status"] == "pending" and item["search_override"] == "better query"


def test_inbox_research_clears_override_and_requeues(grab):
    client, st, _ = grab
    iid = _awaiting_item(st.db)
    csrf = {"X-CSRF-Token": client._csrf}
    # Give it an override first, then "Search again" should clear it back to default.
    client.post(f"/api/inbox/{iid}/search", json={"query": "x"}, headers=csrf)
    st.db.transition(iid, "awaiting_user", "reset for test")
    r = client.post(f"/api/inbox/{iid}/research", headers=csrf)
    assert r.status_code == 200
    item = st.db.get_grab_item(iid)
    assert item["status"] == "pending" and not item["search_override"]


def test_inbox_research_requires_csrf(grab):
    client, st, _ = grab
    iid = _awaiting_item(st.db)
    assert client.post(f"/api/inbox/{iid}/research").status_code == 403


def test_queue_retry_failed_requeues_all(grab):
    client, st, _ = grab
    ids = [st.db.enqueue_grab({"spotify_track_id": f"f{i}", "title": f"T{i}", "artist": "A"})
           for i in range(3)]
    for iid in ids:
        st.db.transition(iid, "failed", "test")
    r = client.post("/api/queue/retry-failed", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200 and r.get_json()["requeued"] == 3
    assert all(st.db.get_grab_item(iid)["status"] == "pending" for iid in ids)


def test_queue_retry_failed_requires_csrf(grab):
    client, _, _ = grab
    assert client.post("/api/queue/retry-failed").status_code == 403


def test_inbox_research_all_requeues_every_awaiting(grab):
    client, st, _ = grab
    ids = []
    for i in range(3):
        iid = st.db.enqueue_grab({"spotify_track_id": f"amb{i}", "title": f"A{i}", "artist": "X"})
        st.db.transition(iid, "awaiting_user", "test")
        ids.append(iid)
    # give one an override to prove it's cleared
    client.post(f"/api/inbox/{ids[0]}/search", json={"query": "x"}, headers={"X-CSRF-Token": client._csrf})
    st.db.transition(ids[0], "awaiting_user", "reset")
    r = client.post("/api/inbox/research-all", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200 and r.get_json()["requeued"] == 3
    assert all(st.db.get_grab_item(i)["status"] == "pending" for i in ids)
    assert not st.db.get_grab_item(ids[0])["search_override"]


def test_inbox_research_all_requires_csrf(grab):
    client, _, _ = grab
    assert client.post("/api/inbox/research-all").status_code == 403


def test_inbox_skip(grab):
    client, st, _ = grab
    iid = _awaiting_item(st.db)
    r = client.post(f"/api/inbox/{iid}/skip", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    assert st.db.get_grab_item(iid)["status"] == "skipped"


def test_inbox_choose_requires_csrf(grab):
    client, st, _ = grab
    iid = _awaiting_item(st.db)
    assert client.post(f"/api/inbox/{iid}/choose", json={"candidate_id": 1}).status_code == 403


def test_export_m3u_lists_matched_tracks(grab):
    client, st, _ = grab
    pid = st.db.add_playlist("PL123", "My Playlist")
    client.post(f"/api/playlists/{pid}/sync", headers={"X-CSRF-Token": client._csrf})
    r = client.get(f"/api/playlists/{pid}/export.m3u")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert body.startswith("#EXTM3U")
    assert "bl.mp3" in body                      # the matched "have" track
    assert "Totally Missing Song" not in body    # missing tracks aren't exported
    # Sync refreshed the name from Spotify ("Playlist PL123"); filename is sanitized.
    assert "filename=\"Playlist_PL123.m3u\"" in r.headers.get("Content-Disposition", "")


def test_duplicates_report(grab):
    import os
    client, st, _ = grab
    for name in ("dup_a.mp3", "dup_b.mp3"):
        p = os.path.join(st.music_dir, name)
        with open(p, "wb") as fh:
            fh.write(b"\x00")
        st.db.upsert_track(p, "1:2", 120.0, None, None, 120.0, 0.9, "librosa", "done")
        st.db.update_track_tags(p, {"title": "Same Song", "artist": "Same Artist",
                                    "norm_title": "same song", "norm_artist": "same artist"}, "1:2")
    groups = client.get("/api/duplicates").get_json()["groups"]
    dup = next((g for g in groups if g["title"] == "same song"), None)
    assert dup is not None and dup["count"] == 2


def test_spotify_search_flags_in_library(grab):
    client, st, _ = grab
    res = client.get("/api/spotify/search?q=weeknd").get_json()["results"]
    bl = next(r for r in res if r["title"] == "Blinding Lights")
    other = next(r for r in res if r["title"] == "Brand New Track")
    assert bl.get("in_library") is True       # matches the seeded library file
    assert not other.get("in_library")


def test_manual_enqueue_and_dedup(grab):
    client, st, _ = grab
    csrf = {"X-CSRF-Token": client._csrf}
    r = client.post("/api/queue", json={"title": "Brand New Track", "artist": "Someone",
                                        "spotify_track_id": "sidX"}, headers=csrf)
    assert r.status_code == 200
    assert st.db.get_queue_counts().get("pending") == 1
    dup = client.post("/api/queue", json={"title": "Brand New Track", "spotify_track_id": "sidX"}, headers=csrf)
    assert dup.status_code == 409


def test_manual_enqueue_requires_csrf(grab):
    client, _, _ = grab
    assert client.post("/api/queue", json={"title": "X"}).status_code == 403


def test_connection_tests_validate(grab):
    client, _, _ = grab
    csrf = {"X-CSRF-Token": client._csrf}
    assert client.post("/api/settings/test-ntfy", json={}, headers=csrf).get_json()["ok"] is False
    assert client.post("/api/settings/test-navidrome", json={}, headers=csrf).get_json()["ok"] is False
    assert client.post("/api/settings/test-monochrome", json={}, headers=csrf).status_code == 400


def test_grabber_status_exposes_versions(grab):
    client, _, _ = grab
    v = client.get("/api/grabber/status").get_json()["versions"]
    assert "app" in v and "yt_dlp" in v


def test_delete_failed_queue_item(grab):
    """A failed item can be removed from the queue; the row and its history go."""
    client, st, _ = grab
    csrf = {"X-CSRF-Token": client._csrf}
    item_id = st.db.enqueue_grab({"title": "Doomed", "artist": "Nobody", "spotify_track_id": "sp_doomed"})
    st.db.transition(item_id, "failed", "no provider match")

    r = client.delete(f"/api/queue/{item_id}", headers=csrf)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert st.db.get_grab_item(item_id) is None
    # Second delete now 404s (already gone).
    assert client.delete(f"/api/queue/{item_id}", headers=csrf).status_code == 404


def test_delete_queue_item_requires_csrf(grab):
    client, st, _ = grab
    item_id = st.db.enqueue_grab({"title": "X", "artist": "Y", "spotify_track_id": "sp_x"})
    st.db.transition(item_id, "failed", "")
    assert client.delete(f"/api/queue/{item_id}").status_code == 403


def test_delete_inflight_queue_item_rejected(grab):
    """An in-flight item must be cancelled, not deleted (would race a worker)."""
    client, st, _ = grab
    csrf = {"X-CSRF-Token": client._csrf}
    item_id = st.db.enqueue_grab({"title": "Busy", "artist": "Z", "spotify_track_id": "sp_busy"})
    st.db.transition(item_id, "downloading", "")
    r = client.delete(f"/api/queue/{item_id}", headers=csrf)
    assert r.status_code == 400 and r.get_json()["error"] == "not_deletable"
    assert st.db.get_grab_item(item_id) is not None


def test_clear_completed_queue(grab):
    """Clear completed removes only 'done' items; failed/pending survive."""
    client, st, _ = grab
    csrf = {"X-CSRF-Token": client._csrf}
    d = st.db.enqueue_grab({"title": "Got it", "artist": "A", "spotify_track_id": "sp_done"})
    st.db.transition(d, "done", "filed")
    f = st.db.enqueue_grab({"title": "Nope", "artist": "B", "spotify_track_id": "sp_fail"})
    st.db.transition(f, "failed", "no match")

    r = client.post("/api/queue/clear-completed", headers=csrf)
    assert r.status_code == 200 and r.get_json()["removed"] == 1
    assert st.db.get_grab_item(d) is None
    assert st.db.get_grab_item(f) is not None


def test_clear_completed_requires_csrf(grab):
    client, _, _ = grab
    assert client.post("/api/queue/clear-completed").status_code == 403
