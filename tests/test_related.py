"""Related endpoints + Deezer catalog client (all network mocked)."""

import os

import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger import matching as m
from bpm_tagger.library_artists import build_library_artists, primary_artist
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




# ── compute (deezer mocked) ──────────────────────────────────────────────────
def _art(name):
    return {"dz_id": "dz:" + name, "name": name, "image_url": name + ".jpg"}


def _trk(tid, title, artist, duration_ms=200000):
    return {"dz_track_id": tid, "title": title, "artist": artist, "album": "Al",
            "duration_ms": duration_ms, "cover_url": "", "preview_url": "prev:" + tid}








def test_build_library_artists_counts_primaries(tmp_path):
    db = BPMDatabase(str(tmp_path / "b.db"))
    _seed(db, "/x1.mp3", "Nine Inch Nails", "Nine Inch Nails", title="x1")
    _seed(db, "/x2.mp3", "Nine Inch Nails", "Nine Inch Nails", title="x2")
    lib = build_library_artists(db)
    disp, count = lib[m.normalize_artist("Nine Inch Nails")]
    assert count == 2 and disp == "Nine Inch Nails"


# ── DB round-trip / migration safety ─────────────────────────────────────────




# ── API ──────────────────────────────────────────────────────────────────────




















# ── API ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def sug(tmp_path):
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(tmp_path / "music"),
        "ui_password": "s3cret", "ui_secret_key": "k",
    }
    os.makedirs(config["music_dir"], exist_ok=True)
    app = create_app(config)
    st = app.extensions["state"]
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st, None


# ── Related endpoints (login-gated) ───────────────────────────────────────────
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


def test_related_with_empty_name_returns_nothing(client, base_config):
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


def test_deezer_search_artists_shapes_and_drops_placeholder(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"], seen["params"] = url, params
        return _Resp({"data": [
            {"id": 1, "name": "Anyma", "picture_xl": "https://e-cdns-images.dzcdn.net/images/artist/abc/1000x1000.jpg",
             "nb_fan": 1234},
            {"id": 2, "name": "Anyma Tribute", "picture_xl": "https://e-cdns-images.dzcdn.net/images/artist//1000x1000.jpg"},
            {"error": "junk"},
        ]})
    monkeypatch.setattr(dc.requests, "get", fake_get)
    out = dc.search_artists("anyma tribute")
    assert seen["url"].endswith("/search/artist") and seen["params"]["q"] == "anyma tribute"
    assert [a["dz_id"] for a in out] == ["2", "1"]  # exact name first
    assert out[1]["fans"] == 1234 and out[1]["image_url"].endswith("1000x1000.jpg")
    assert out[0]["image_url"] == "" and out[0]["fans"] == 0  # placeholder dropped
    assert dc.search_artists("  ") == []


def test_deezer_namesakes_prefer_most_followed(monkeypatch):
    # Deezer can rank a tiny namesake above the artist you meant.
    hits = {"data": [
        {"id": 1, "name": "Anyma", "nb_fan": 18},
        {"id": 2, "name": "Anyma (UK)", "nb_fan": 130},
        {"id": 3, "name": "Anyma", "nb_fan": 45100},
    ]}
    monkeypatch.setattr(dc.requests, "get", lambda url, params=None, timeout=None: _Resp(hits))
    assert [a["dz_id"] for a in dc.search_artists("Anyma")] == ["3", "1", "2"]
    assert dc.search_artist("Anyma")["dz_id"] == "3"


def test_deezer_search_artists_endpoint_flags_library(sug, monkeypatch):
    client, st, _ = sug
    _seed(st.db, os.path.join(st.music_dir, "owned.mp3"), "Owner", "Owner", title="Owned Song")
    calls = {"n": 0}

    def fake_search(q, limit=6):
        calls["n"] += 1
        return [dict(_art("Owner"), fans=5), dict(_art("Stranger"), fans=1)]
    monkeypatch.setattr(dc, "search_artists", fake_search)
    arts = client.get("/api/deezer/search-artists?q=Own").get_json()["artists"]
    assert [a["name"] for a in arts] == ["Owner", "Stranger"]
    assert arts[0]["track_count"] == 1 and arts[0]["library_name"] == "Owner"
    assert arts[1]["track_count"] == 0 and "library_name" not in arts[1]
    client.get("/api/deezer/search-artists?q=own")  # same query, other case → cached
    assert calls["n"] == 1
    assert client.get("/api/deezer/search-artists?q=").get_json() == {"artists": []}


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






def test_description_endpoint(sug, monkeypatch):
    client, _, _ = sug
    import bpm_tagger.integrations.artist_info as ai
    monkeypatch.setattr(ai, "artist_bio", lambda name: "A test rock band.")
    body = client.get("/api/related/description?name=Bio Test Band").get_json()
    assert body["description"] == "A test rock band."
    assert client.get("/api/related/description?name=").get_json()["description"] == ""
