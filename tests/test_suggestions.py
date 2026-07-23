"""Suggestions engine + API + Deezer catalog client (all network mocked)."""

import os

import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber import matching as m
from bpm_tagger.grabber.suggestions import SuggestionsEngine, build_library_artists, primary_artist
from bpm_tagger.integrations import deezer_catalog as dc
from bpm_tagger.web.app import create_app


# ── deezer_catalog: shaping + failure handling (requests mocked) ─────────────
class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


def test_deezer_top_tracks_seconds_to_ms(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _Resp({"data": [{"id": 1, "title": "S", "duration": 210,
                                "artist": {"name": "A"},
                                "album": {"title": "Al", "cover_xl": "c"},
                                "preview": "prev"}]})
    monkeypatch.setattr(dc.requests, "get", fake_get)
    tracks = dc.artist_top_tracks("99")
    assert tracks[0]["duration_ms"] == 210000        # seconds → ms
    assert tracks[0]["preview_url"] == "prev"
    assert tracks[0]["cover_url"] == "c"


def test_deezer_search_artist_prefers_exact_norm(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _Resp({"data": [{"id": 2, "name": "Weeknd Tribute", "picture": "p2"},
                               {"id": 3, "name": "The Weeknd", "picture": "p3"}]})
    monkeypatch.setattr(dc.requests, "get", fake_get)
    hit = dc.search_artist("The Weeknd")
    assert hit["dz_id"] == "3"                        # exact normalized match wins


def test_deezer_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(dc.requests, "get", boom)
    assert dc.search_artist("x") is None
    assert dc.related_artists("1") == []
    assert dc.artist_top_tracks("1") == []
    assert dc.artist_radio("1") == []
    assert dc.track_isrc("1") == ""


# ── seed selection (pure) ────────────────────────────────────────────────────
def test_primary_artist_album_artist_fallback_and_split():
    assert primary_artist("X feat. Y", "Album Artist") == "Album Artist"
    assert primary_artist("X feat. Y", "") == "X"
    assert primary_artist("A, B & C", "") == "A"


def _seed(db, path, artist, album_artist="", starred=False, title="T",
          album="Alb", duration_ms=200000, isrc=""):
    db.upsert_track(path, "1:1", 120.0, None, None, 120.0, 0.9, "librosa", "done")
    db.update_track_tags(path, {
        "title": title, "artist": artist, "album": album, "album_artist": album_artist,
        "track_no": 1, "disc_no": 1, "year": 2020, "isrc": isrc, "duration_ms": duration_ms,
        "norm_title": m.normalize_title(title), "norm_artist": m.normalize_artist(artist),
    }, "1:1")
    if starred:
        db.set_starred(path, True)


def test_seed_weighting_prefers_starred(tmp_path):
    db = BPMDatabase(str(tmp_path / "b.db"))
    _seed(db, "/a1.mp3", "Alpha", "Alpha", title="a1")
    _seed(db, "/a2.mp3", "Alpha", "Alpha", title="a2")   # Alpha: 2 tracks, weight 2
    _seed(db, "/b1.mp3", "Beta", "Beta", starred=True, title="b1")  # Beta: 1 starred, weight 6
    engine = SuggestionsEngine({}, db)
    seeds = engine._seeds_from_rows(db.get_artist_index_rows())
    assert seeds[0]["name"] == "Beta"                    # starred outweighs 2 plain tracks
    assert [s["name"] for s in seeds] == ["Beta", "Alpha"]


# ── compute (deezer mocked) ──────────────────────────────────────────────────
def _art(name):
    return {"dz_id": "dz:" + name, "name": name, "image_url": name + ".jpg"}


def _trk(tid, title, artist, duration_ms=200000):
    return {"dz_track_id": tid, "title": title, "artist": artist, "album": "Al",
            "duration_ms": duration_ms, "cover_url": "", "preview_url": "prev:" + tid}


def _mock_deezer(monkeypatch):
    related = {
        "dz:Radiohead": [_art("Muse"), _art("Coldplay"), _art("Portishead")],
        "dz:Muse": [_art("Radiohead"), _art("Coldplay")],
    }
    top = {
        "dz:Coldplay": [_trk("c1", "Yellow", "Coldplay", 180000)],
        # Starlight matches the seeded Muse library track → filtered; Hysteria kept;
        # m3 is dismissed in the test.
        "dz:Muse": [_trk("m1", "Starlight", "Muse", 200000),
                    _trk("m2", "Hysteria", "Muse", 180000),
                    _trk("m3", "Uprising", "Muse", 180000)],
    }
    monkeypatch.setattr(dc, "search_artist", lambda name: _art(name))
    monkeypatch.setattr(dc, "related_artists", lambda dz_id: related.get(dz_id, []))
    monkeypatch.setattr(dc, "artist_top_tracks", lambda dz_id, limit=5: top.get(dz_id, [])[:limit])


def _compute_fixture(tmp_path, monkeypatch):
    db = BPMDatabase(str(tmp_path / "b.db"))
    for i in range(5):                                   # Radiohead: owned (>=3), starred
        _seed(db, f"/rh{i}.mp3", "Radiohead", "Radiohead", starred=True, title=f"rh{i}")
    _seed(db, "/muse.mp3", "Muse", "Muse", title="Starlight")  # Muse: sampled (1 track)
    _mock_deezer(monkeypatch)
    return db


def test_compute_scores_filters_and_persists(tmp_path, monkeypatch):
    db = _compute_fixture(tmp_path, monkeypatch)
    db.dismiss_suggestion("artist", m.normalize_artist("Portishead"))
    db.dismiss_suggestion("track", "m3")
    # A stale suggestion that a fresh compute must replace.
    db.replace_suggestions([{"dz_id": "old", "name": "Old Band", "image_url": "",
                             "score": 9, "have_tracks": 0, "seeds": []}], [])

    SuggestionsEngine({}, db).compute()

    artists = {a["name"]: a for a in db.get_suggestions("artist")}
    assert set(artists) == {"Coldplay", "Muse"}          # owned Radiohead + dismissed Portishead gone
    assert "Old Band" not in artists                     # prior rows replaced
    assert artists["Muse"]["have_tracks"] == 1           # sampled artist kept, badged
    assert artists["Coldplay"]["have_tracks"] == 0
    # Coldplay surfaced by both seeds → higher score than Muse (one seed).
    assert artists["Coldplay"]["score"] > artists["Muse"]["score"]

    tracks = {t["name"] for t in db.get_suggestions("track")}
    assert tracks == {"Yellow", "Hysteria"}              # Starlight (owned) + Uprising (dismissed) filtered


def test_build_library_artists_counts_primaries(tmp_path):
    db = BPMDatabase(str(tmp_path / "b.db"))
    _seed(db, "/x1.mp3", "Nine Inch Nails", "Nine Inch Nails", title="x1")
    _seed(db, "/x2.mp3", "Nine Inch Nails", "Nine Inch Nails", title="x2")
    lib = build_library_artists(db)
    disp, count = lib[m.normalize_artist("Nine Inch Nails")]
    assert count == 2 and disp == "Nine Inch Nails"


# ── DB round-trip / migration safety ─────────────────────────────────────────
def test_db_migration_additive_and_dismiss_survives_recompute(tmp_path):
    path = str(tmp_path / "b.db")
    db = BPMDatabase(path)
    db.replace_suggestions([{"dz_id": "a1", "name": "Keep", "image_url": "",
                             "score": 1, "have_tracks": 0, "seeds": ["S"]}], [])
    # Re-opening an existing DB re-runs the additive CREATE ... IF NOT EXISTS.
    db2 = BPMDatabase(path)
    assert [a["name"] for a in db2.get_suggestions("artist")] == ["Keep"]

    db2.dismiss_suggestion("artist", m.normalize_artist("Keep"))
    assert db2.get_suggestions("artist") == []           # dismiss pruned the row
    assert m.normalize_artist("Keep") in db2.get_dismissed_suggestion_keys("artist")

    # A later recompute that re-surfaces "Keep" must still drop it (dismissed).
    db2.replace_suggestions([{"dz_id": "a1", "name": "Keep", "image_url": "",
                              "score": 1, "have_tracks": 0, "seeds": []}], [])
    # replace_suggestions is unconditional; the engine is what honors dismissals,
    # but the dismissed key persists for it to consult.
    assert m.normalize_artist("Keep") in db2.get_dismissed_suggestion_keys("artist")


def test_mark_suggestion_queued(tmp_path):
    db = BPMDatabase(str(tmp_path / "b.db"))
    db.replace_suggestions([], [{"dz_track_id": "t1", "title": "T", "artist": "A",
                                 "album": "", "duration_ms": 200000, "cover_url": "",
                                 "preview_url": "", "score": 1, "seeds": []}])
    row = db.get_suggestions("track")[0]
    assert row["queued_at"] is None
    db.mark_suggestion_queued(row["id"])
    assert db.get_suggestions("track")[0]["queued_at"] is not None


# ── API ──────────────────────────────────────────────────────────────────────
class _FakeClient:
    def __init__(self):
        self.connected = True

    def is_connected(self):
        return self.connected

    def search_tracks(self, query, limit=20):
        # Confident match for the Weeknd enqueue test; nothing otherwise.
        if "weeknd" in query.lower():
            return [{"spotify_track_id": "spot1", "title": "Blinding Lights",
                     "artist": "The Weeknd", "album": "After Hours", "album_artist": "The Weeknd",
                     "duration_ms": 200000, "isrc": "USUG11904206", "track_no": 1,
                     "disc_no": 1, "year": 2020, "cover_url": "",
                     "norm_title": m.normalize_title("Blinding Lights"),
                     "norm_artist": m.normalize_artist("The Weeknd")}]
        return []


class _Tagger:
    def __init__(self, db):
        self.db = db
        self.notifier = None
        self.grabber = None

    def index_tags(self):
        return 0


@pytest.fixture
def sug(tmp_path):
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(tmp_path / "music"),
        "ui_password": "s3cret", "ui_secret_key": "k",
        "grabber_enabled": True,
        "spotify_client_id": "cid", "spotify_client_secret": "sec",
        "spotify_redirect_uri": "http://localhost:5000/api/spotify/callback",
    }
    os.makedirs(config["music_dir"], exist_ok=True)
    from bpm_tagger.grabber.sync_engine import GrabberService
    app = create_app(config)
    st = app.extensions["state"]
    tagger = _Tagger(st.db)
    service = GrabberService(config, st.db, tagger, None)
    fake = _FakeClient()
    service.client = fake
    service.sync.client = fake
    tagger.grabber = service
    st.tagger = tagger

    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st, fake


def test_suggestions_disabled_without_grabber(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.get("/api/suggestions").get_json()["enabled"] is False


def test_suggestions_get_shape_and_flags(sug):
    client, st, _ = sug
    _seed(st.db, os.path.join(st.music_dir, "owned.mp3"), "Owner", "Owner",
          title="Owned Song")
    st.db.replace_suggestions(
        [{"dz_id": "a1", "name": "Coldplay", "image_url": "", "score": 1.0,
          "have_tracks": 0, "seeds": ["Radiohead"]}],
        [{"dz_track_id": "t1", "title": "Owned Song", "artist": "Owner", "album": "",
          "duration_ms": 200000, "cover_url": "", "preview_url": "p", "score": 1.0, "seeds": []},
         {"dz_track_id": "t2", "title": "New Song", "artist": "Newbie", "album": "",
          "duration_ms": 200000, "cover_url": "", "preview_url": "p2", "score": 0.9, "seeds": []}])
    new_id = next(r["id"] for r in st.db.get_suggestions("track") if r["name"] == "New Song")
    st.db.mark_suggestion_queued(new_id)                   # queue one

    body = client.get("/api/suggestions").get_json()
    assert body["enabled"] is True and body["refreshing"] is False
    assert body["computed_at"] and body["seed_count"] == 1
    assert body["artists"][0]["name"] == "Coldplay"
    tracks = {t["title"]: t for t in body["tracks"]}
    assert tracks["Owned Song"]["in_library"] is True
    assert tracks["New Song"]["in_library"] is False
    assert tracks["New Song"]["queued"] is True            # queued_at was set
    assert tracks["Owned Song"]["queued"] is False


def test_refresh_returns_409_while_running(sug):
    client, st, _ = sug
    st.tagger.grabber.suggestions._refreshing = True
    r = client.post("/api/suggestions/refresh", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 409 and r.get_json()["error"] == "already_refreshing"


def test_refresh_requires_csrf(sug):
    client, _, _ = sug
    assert client.post("/api/suggestions/refresh").status_code == 403


def test_dismiss_requires_csrf_and_persists(sug):
    client, st, _ = sug
    assert client.post("/api/suggestions/dismiss",
                       json={"kind": "track", "key": "t1"}).status_code == 403
    r = client.post("/api/suggestions/dismiss", json={"kind": "track", "key": "t1"},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    assert "t1" in st.db.get_dismissed_suggestion_keys("track")


def test_queue_adopts_spotify_meta_and_dedupes(sug):
    client, st, _ = sug
    st.db.replace_suggestions([], [{"dz_track_id": "dz9", "title": "Blinding Lights",
                                    "artist": "The Weeknd", "album": "After Hours",
                                    "duration_ms": 200000, "cover_url": "", "preview_url": "",
                                    "score": 1, "seeds": []}])
    sid = st.db.get_suggestions("track")[0]["id"]
    body = {"dz_track_id": "dz9", "title": "Blinding Lights", "artist": "The Weeknd",
            "album": "After Hours", "duration_ms": 200000, "suggestion_id": sid}
    r = client.post("/api/suggestions/queue", json=body, headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    item = st.db.get_queue("pending")[0]
    assert item["spotify_track_id"] == "spot1"             # adopted from Spotify search
    assert item["isrc"] == "USUG11904206"
    assert st.db.get_suggestions("track")[0]["queued_at"] is not None
    # Second enqueue dedupes on the adopted spotify_track_id.
    dup = client.post("/api/suggestions/queue", json=body, headers={"X-CSRF-Token": client._csrf})
    assert dup.status_code == 409


def test_queue_falls_back_to_deezer_isrc(sug, monkeypatch):
    client, st, fake = sug
    fake.connected = False                                 # no Spotify enrichment
    monkeypatch.setattr(dc, "track_isrc", lambda tid: "GBTEST1234567")
    r = client.post("/api/suggestions/queue",
                    json={"dz_track_id": "dz5", "title": "Some Track", "artist": "Nobody",
                          "duration_ms": 180000},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    item = st.db.get_queue("pending")[0]
    assert item["isrc"] == "GBTEST1234567" and not item["spotify_track_id"]


# ── Part B: related endpoints (login-gated, NOT grabber-gated) ────────────────
def test_related_artists_track_counts(sug, monkeypatch):
    client, st, _ = sug
    for i in range(3):                                     # own 3 by Coldplay
        _seed(st.db, os.path.join(st.music_dir, f"c{i}.mp3"), "Coldplay", "Coldplay", title=f"c{i}")
    _seed(st.db, os.path.join(st.music_dir, "m.mp3"), "Muse", "Muse", title="Starlight")  # 1 by Muse
    monkeypatch.setattr(dc, "search_artist", lambda name: _art(name))
    monkeypatch.setattr(dc, "related_artists",
                        lambda dz_id: [_art("Coldplay"), _art("Muse"), _art("Unknown Band")])
    arts = {a["name"]: a for a in client.get("/api/related/artists?name=Radiohead").get_json()["artists"]}
    assert arts["Coldplay"]["track_count"] == 3 and arts["Coldplay"]["library_name"] == "Coldplay"
    assert arts["Muse"]["track_count"] == 1                # sampled, not excluded
    assert arts["Unknown Band"]["track_count"] == 0 and "library_name" not in arts["Unknown Band"]


def test_related_tracks_flags_and_cache(sug, monkeypatch):
    client, st, _ = sug
    _seed(st.db, os.path.join(st.music_dir, "own.mp3"), "Owner", "Owner",
          title="Owned Song", duration_ms=200000)
    calls = {"n": 0}

    def fake_radio(dz_id, limit=25):
        calls["n"] += 1
        return [_trk("r1", "Owned Song", "Owner", 200000), _trk("r2", "Fresh Cut", "Stranger")]
    monkeypatch.setattr(dc, "search_artist", lambda name: _art(name))
    monkeypatch.setattr(dc, "artist_radio", fake_radio)

    tracks = {t["title"]: t for t in client.get("/api/related/tracks?name=Owner").get_json()["tracks"]}
    assert tracks["Owned Song"]["in_library"] is True and tracks["Owned Song"]["file_path"]
    assert tracks["Owned Song"]["bpm"] == 120.0     # Part D: enqueue-to-play-queue needs it
    assert tracks["Fresh Cut"]["in_library"] is False
    assert "bpm" not in tracks["Fresh Cut"]
    # Second call for the same normalized artist hits the cache (no re-fetch).
    client.get("/api/related/tracks?name=owner")
    assert calls["n"] == 1


def test_related_reachable_with_grabber_disabled(client, base_config):
    # The default conftest client has no grabber; related endpoints still work.
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.get("/api/related/artists?name=").get_json() == {"artists": []}
    assert client.get("/api/related/tracks?name=").get_json() == {"tracks": []}


def test_deezer_resolve_endpoint(sug, monkeypatch):
    client, _, _ = sug
    calls = {"n": 0}

    def fake_search(name):
        calls["n"] += 1
        return _art(name) if name == "ResolveMe" else None
    monkeypatch.setattr(dc, "search_artist", fake_search)
    body = client.get("/api/deezer/resolve?name=ResolveMe").get_json()
    assert body["artist"]["dz_id"] == "dz:ResolveMe"
    # Same normalized name hits the cache — no second Deezer call.
    client.get("/api/deezer/resolve?name=resolveme")
    assert calls["n"] == 1
    assert client.get("/api/deezer/resolve?name=").get_json() == {"artist": None}
    # No Deezer match resolves to null, not an error.
    assert client.get("/api/deezer/resolve?name=NopeUnknownXyz").get_json()["artist"] is None


# ── Deezer catalog: artist / albums / album shaping ──────────────────────────
def test_deezer_album_seconds_to_ms(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _Resp({"id": 10, "title": "Alb", "cover_xl": "c", "record_type": "album",
                      "release_date": "2019-05-01", "artist": {"name": "A"},
                      "tracks": {"data": [{"id": 1, "title": "S", "duration": 200,
                                           "preview": "p", "artist": {"name": "A"}}]}})
    monkeypatch.setattr(dc.requests, "get", fake_get)
    alb = dc.album("10")
    assert alb["title"] == "Alb" and alb["year"] == 2019 and alb["record_type"] == "album"
    t = alb["tracks"][0]
    assert t["duration_ms"] == 200000 and t["album"] == "Alb" and t["cover_url"] == "c"


def test_deezer_artist_albums_split_record_type(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _Resp({"data": [
            {"id": 1, "title": "LP", "record_type": "album", "release_date": "2020-01-01",
             "nb_tracks": 10, "cover": "c"},
            {"id": 2, "title": "Sng", "record_type": "single", "release_date": "2021-01-01",
             "nb_tracks": 1, "cover": "c"},
        ]})
    monkeypatch.setattr(dc.requests, "get", fake_get)
    albs = dc.artist_albums("5")
    assert {a["record_type"] for a in albs} == {"album", "single"}
    assert next(a for a in albs if a["title"] == "LP")["year"] == 2020


# ── API: artist detail / album / queue-album / description ────────────────────
def test_deezer_artist_endpoint_splits_and_flags(sug, monkeypatch):
    client, st, _ = sug
    _seed(st.db, os.path.join(st.music_dir, "own.mp3"), "A", "A", title="Owned")
    monkeypatch.setattr(dc, "get_artist",
                        lambda i: {"dz_id": i, "name": "A", "image_url": "", "nb_fan": 10, "nb_album": 2})
    monkeypatch.setattr(dc, "artist_top_tracks",
                        lambda i, limit=10: [_trk("t1", "Owned", "A"), _trk("t2", "New", "A")])
    monkeypatch.setattr(dc, "artist_albums", lambda i, limit=100: [
        {"dz_album_id": "al1", "title": "LP", "cover_url": "", "record_type": "album",
         "year": 2020, "release_date": "", "nb_tracks": 10, "explicit": False},
        {"dz_album_id": "al2", "title": "Sng", "cover_url": "", "record_type": "single",
         "year": 2021, "release_date": "", "nb_tracks": 1, "explicit": False},
    ])
    body = client.get("/api/deezer/artist/99").get_json()
    assert body["artist"]["name"] == "A" and body["artist"]["nb_fan"] == 10
    assert [a["title"] for a in body["albums"]] == ["LP"]
    assert [a["title"] for a in body["singles"]] == ["Sng"]
    tt = {t["title"]: t for t in body["top_tracks"]}
    assert tt["Owned"]["in_library"] is True and tt["New"]["in_library"] is False


def test_deezer_album_endpoint_flags_tracks(sug, monkeypatch):
    client, st, _ = sug
    _seed(st.db, os.path.join(st.music_dir, "own.mp3"), "A", "A", title="Owned")
    monkeypatch.setattr(dc, "album", lambda aid: {
        "dz_album_id": aid, "title": "LP", "cover_url": "", "record_type": "album",
        "year": 2020, "artist": "A", "tracks": [_trk("t1", "Owned", "A"), _trk("t2", "New", "A")]})
    body = client.get("/api/deezer/album/albX").get_json()
    tt = {t["title"]: t for t in body["album"]["tracks"]}
    assert tt["Owned"]["in_library"] is True and tt["New"]["in_library"] is False


def test_queue_album_enqueues_missing_only(sug, monkeypatch):
    client, st, _ = sug
    _seed(st.db, os.path.join(st.music_dir, "own.mp3"), "A", "A", title="Owned")
    monkeypatch.setattr(dc, "album", lambda aid: {
        "dz_album_id": aid, "title": "LP", "cover_url": "", "record_type": "album",
        "year": 2020, "artist": "A",
        "tracks": [_trk("t1", "Owned", "A"), _trk("t2", "New Song", "A"), _trk("t3", "Another", "A")]})
    r = client.post("/api/suggestions/queue-album", json={"album_id": "albQ"},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    j = r.get_json()
    assert j["enqueued"] == 2 and j["total"] == 3       # the owned track is skipped
    assert st.db.get_queue_counts().get("pending") == 2


def test_queue_album_requires_csrf(sug):
    client, _, _ = sug
    assert client.post("/api/suggestions/queue-album", json={"album_id": "x"}).status_code == 403


def test_description_endpoint(sug, monkeypatch):
    client, _, _ = sug
    import bpm_tagger.integrations.artist_info as ai
    monkeypatch.setattr(ai, "artist_bio", lambda name: "A test rock band.")
    body = client.get("/api/related/description?name=Bio Test Band").get_json()
    assert body["description"] == "A test rock band."
    assert client.get("/api/related/description?name=").get_json()["description"] == ""
