"""Metadata lookup: integrations.metadata gathering + /api/metadata/lookup."""

import pytest

import bpm_tagger.integrations.metadata as metadata_mod
import bpm_tagger.web.api.tracks as tracks_mod
from bpm_tagger.integrations.metadata import gather_metadata
from bpm_tagger.web.app import create_app

DEEZER_TRACK = {
    "id": 3129407, "title": "Dancing Queen", "title_short": "Dancing Queen",
    "isrc": "sepqa7600014", "duration": 231, "track_position": 2, "disk_number": 1,
    "release_date": "1976-10-11", "link": "https://www.deezer.com/track/3129407",
    "artist": {"name": "ABBA"},
    "album": {"title": "Arrival", "cover_xl": "https://cdn.dzcdn.net/arrival-xl.jpg"},
}


class _FakeResp:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


# ── gather_metadata (Deezer, no Spotify) ─────────────────────────────────────

def test_gather_by_isrc_normalizes_deezer_track(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        assert "track/isrc:SEPQA7600014" in url
        return _FakeResp(DEEZER_TRACK)

    monkeypatch.setattr(metadata_mod.requests, "get", fake_get)
    cands = gather_metadata(None, isrc="SEPQA7600014")
    assert len(cands) == 1
    c = cands[0]
    assert c == {
        "source": "deezer", "title": "Dancing Queen", "artist": "ABBA",
        "album": "Arrival", "album_artist": "ABBA", "track_no": 2, "disc_no": 1,
        "year": 1976, "isrc": "SEPQA7600014", "duration_ms": 231000,
        "cover_url": "https://cdn.dzcdn.net/arrival-xl.jpg",
        "url": "https://www.deezer.com/track/3129407",
    }
    assert len(calls) == 1  # ISRC endpoint returns full details in one call


def test_gather_by_isrc_miss_returns_empty(monkeypatch):
    monkeypatch.setattr(metadata_mod.requests, "get",
                        lambda url, **kw: _FakeResp({"error": {"code": 800}}))
    assert gather_metadata(None, isrc="XXAAA0000000") == []


def test_gather_search_fetches_details_per_hit(monkeypatch):
    def fake_get(url, params=None, **kw):
        if "search" in url:
            assert 'artist:"ABBA"' in params["q"] and 'track:"Dancing Queen"' in params["q"]
            return _FakeResp({"data": [{"id": 3129407}, {"id": 999}]})
        if url.endswith("track/3129407"):
            return _FakeResp(DEEZER_TRACK)
        return _FakeResp({"error": {"code": 800}})  # second hit gone → skipped

    monkeypatch.setattr(metadata_mod.requests, "get", fake_get)
    cands = gather_metadata(None, artist="ABBA", title="Dancing Queen")
    assert len(cands) == 1 and cands[0]["track_no"] == 2


def test_gather_uses_spotify_isrc_query(monkeypatch):
    class FakeClient:
        def is_connected(self):
            return True

        def search_tracks(self, query, limit=4):
            assert query == "isrc:SEPQA7600014"
            return [{"spotify_track_id": "sp1", "title": "Dancing Queen", "artist": "ABBA",
                     "album": "Arrival", "album_artist": "ABBA", "duration_ms": 230000,
                     "isrc": "SEPQA7600014", "track_no": 2, "disc_no": 1, "year": 1976,
                     "cover_url": "https://i.scdn.co/arrival.jpg"}]

    monkeypatch.setattr(metadata_mod.requests, "get",
                        lambda url, **kw: _FakeResp({"error": {"code": 800}}))
    cands = gather_metadata(FakeClient(), isrc="SEPQA7600014")
    spotify = [c for c in cands if c["source"] == "spotify"]
    assert len(spotify) == 1
    assert spotify[0]["url"] == "https://open.spotify.com/track/sp1"
    assert spotify[0]["album_artist"] == "ABBA"


# ── endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def meta_client(tmp_path):
    config = {
        "db_path": str(tmp_path / "bpm.db"), "music_dir": str(tmp_path / "music"),
        "ui_password": "s3cret", "ui_secret_key": "k",
    }
    (tmp_path / "music").mkdir()
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    return client


def test_lookup_requires_login(meta_client):
    fresh = meta_client.application.test_client()
    assert fresh.get("/api/metadata/lookup?artist=ABBA&title=SOS").status_code == 401


def test_lookup_passes_params(meta_client, monkeypatch):
    seen = {}

    def fake_gather(client, artist="", title="", isrc="", q=""):
        seen.update(artist=artist, title=title, isrc=isrc, q=q)
        return [{"source": "deezer", "title": "SOS"}]

    monkeypatch.setattr(tracks_mod, "gather_metadata", fake_gather)
    r = meta_client.get("/api/metadata/lookup?artist=ABBA&title=SOS")
    assert r.status_code == 200
    assert r.get_json()["candidates"][0]["title"] == "SOS"
    assert seen == {"artist": "ABBA", "title": "SOS", "isrc": "", "q": ""}


def test_lookup_normalizes_and_validates_isrc(meta_client, monkeypatch):
    seen = {}
    monkeypatch.setattr(tracks_mod, "gather_metadata",
                        lambda client, **kw: seen.update(kw) or [])
    r = meta_client.get("/api/metadata/lookup?isrc=se-pqa 76-00014")
    assert r.status_code == 200
    assert seen["isrc"] == "SEPQA7600014"
    assert meta_client.get("/api/metadata/lookup?isrc=NOTVALID").status_code == 400


def test_lookup_empty_params_returns_nothing(meta_client):
    r = meta_client.get("/api/metadata/lookup")
    assert r.status_code == 200 and r.get_json() == {"candidates": []}
