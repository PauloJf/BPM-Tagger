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
