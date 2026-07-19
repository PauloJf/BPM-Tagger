"""Inbox candidate + source-track preview endpoints.

Lazy Deezer preview-URL resolution with a module-level TTL cache. All Deezer
calls are monkeypatched — no network. The module caches are cleared between
tests by the fixture so a hit/miss counter test is deterministic.
"""

import pytest

from bpm_tagger.integrations import deezer_catalog
from bpm_tagger.web.api import inbox as inbox_api


@pytest.fixture
def preview(app):
    """Authenticated client + state, with the preview caches cleared."""
    st = app.extensions["state"]
    inbox_api._cand_preview_cache.clear()
    inbox_api._src_preview_cache.clear()
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st


def _awaiting_with_candidate(db, provider="deezer", provider_track_id="777",
                             url="", isrc="USABC1234567"):
    """An awaiting_user item carrying one candidate; returns (item_id, cand_id)."""
    iid = db.enqueue_grab({"spotify_track_id": f"sp_{provider}_{provider_track_id}",
                           "title": "Song", "artist": "Artist", "isrc": isrc})
    db.transition(iid, "awaiting_user", "test")
    db.add_grab_candidates(iid, [{
        "provider": provider, "provider_track_id": provider_track_id,
        "title": "Song", "artist": "Artist", "album": "", "duration_ms": 200000,
        "isrc": "", "quality": "MP3_320", "score": 0.8, "score_breakdown": "{}",
        "url": url, "cover_url": "", "rank": 0,
    }])
    return iid, db.get_grab_candidates(iid)[0]["id"]


# ── Part A — candidate previews ──────────────────────────────────────────────
def test_candidate_preview_deezer_returns_url(preview, monkeypatch):
    client, st = preview
    _, cid = _awaiting_with_candidate(st.db, provider_track_id="777")
    monkeypatch.setattr(deezer_catalog, "track_preview_url",
                        lambda tid: f"https://cdns-preview.dzcdn.net/{tid}.mp3")
    body = client.get(f"/api/inbox/candidates/{cid}/preview").get_json()
    assert body["preview_url"] == "https://cdns-preview.dzcdn.net/777.mp3"
    assert body["dz_track_id"] == "777"


def test_candidate_preview_ytdlp_is_empty_and_uncalled(preview, monkeypatch):
    client, st = preview
    _, cid = _awaiting_with_candidate(st.db, provider="ytdlp", provider_track_id="",
                                      url="https://youtube.com/watch?v=x")
    calls = []
    monkeypatch.setattr(deezer_catalog, "track_preview_url",
                        lambda tid: calls.append(tid) or "x")
    body = client.get(f"/api/inbox/candidates/{cid}/preview").get_json()
    assert body == {"preview_url": "", "dz_track_id": ""}
    assert calls == []   # non-Deezer never hits the catalog


def test_candidate_preview_unknown_id_404(preview):
    client, _ = preview
    assert client.get("/api/inbox/candidates/999999/preview").status_code == 404


def test_candidate_preview_cache_single_call(preview, monkeypatch):
    client, st = preview
    _, cid = _awaiting_with_candidate(st.db, provider_track_id="777")
    calls = []
    monkeypatch.setattr(deezer_catalog, "track_preview_url",
                        lambda tid: (calls.append(tid), "u")[1])
    a = client.get(f"/api/inbox/candidates/{cid}/preview").get_json()
    b = client.get(f"/api/inbox/candidates/{cid}/preview").get_json()
    assert a == b == {"preview_url": "u", "dz_track_id": "777"}
    assert len(calls) == 1   # second click served from cache


def test_candidate_preview_empty_result_is_cached(preview, monkeypatch):
    """A Deezer track with no preview (or a lookup failure returning "") caches
    the empty result — the catalog isn't re-queried on the next click."""
    client, st = preview
    _, cid = _awaiting_with_candidate(st.db, provider_track_id="777")
    calls = []
    monkeypatch.setattr(deezer_catalog, "track_preview_url",
                        lambda tid: (calls.append(tid), "")[1])
    assert client.get(f"/api/inbox/candidates/{cid}/preview").get_json()["preview_url"] == ""
    assert client.get(f"/api/inbox/candidates/{cid}/preview").get_json()["preview_url"] == ""
    assert len(calls) == 1


def test_candidate_preview_requires_auth(app):
    """Unauthenticated request is refused (login_required)."""
    st = app.extensions["state"]
    inbox_api._cand_preview_cache.clear()
    _, cid = _awaiting_with_candidate(st.db, provider_track_id="777")
    client = app.test_client()
    r = client.get(f"/api/inbox/candidates/{cid}/preview")
    assert r.status_code in (401, 403) or r.status_code in (301, 302)


# ── Part B — source-track preview ────────────────────────────────────────────
def test_source_preview_resolves_isrc(preview, monkeypatch):
    client, st = preview
    iid, _ = _awaiting_with_candidate(st.db, isrc="GBGBG1234567")
    monkeypatch.setattr(deezer_catalog, "track_by_isrc",
                        lambda isrc: {"dz_track_id": "555", "preview_url": f"u:{isrc}"})
    body = client.get(f"/api/inbox/{iid}/source-preview").get_json()
    assert body["preview_url"] == "u:GBGBG1234567"
    assert body["dz_track_id"] == f"src:{iid}"


def test_source_preview_no_isrc_is_empty_and_uncalled(preview, monkeypatch):
    client, st = preview
    iid, _ = _awaiting_with_candidate(st.db, isrc="")
    calls = []
    monkeypatch.setattr(deezer_catalog, "track_by_isrc",
                        lambda isrc: calls.append(isrc) or {})
    body = client.get(f"/api/inbox/{iid}/source-preview").get_json()
    assert body == {"preview_url": "", "dz_track_id": ""}
    assert calls == []


def test_source_preview_unknown_item_404(preview):
    client, _ = preview
    assert client.get("/api/inbox/999999/source-preview").status_code == 404


def test_source_preview_cache_single_call(preview, monkeypatch):
    client, st = preview
    iid, _ = _awaiting_with_candidate(st.db, isrc="USUSU1234567")
    calls = []
    monkeypatch.setattr(deezer_catalog, "track_by_isrc",
                        lambda isrc: (calls.append(isrc), {"dz_track_id": "5", "preview_url": "u"})[1])
    client.get(f"/api/inbox/{iid}/source-preview")
    client.get(f"/api/inbox/{iid}/source-preview")
    assert len(calls) == 1
