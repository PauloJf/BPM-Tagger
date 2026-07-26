"""Playlist management: rename, description, pinning.

Renaming is deliberately Local-only — `mark_playlist_synced` / `update_playlist_sync`
overwrite `name` on every sync, so a renamed Spotify or Navidrome mirror would
silently revert on the next poll. Description and pinned are never touched by sync,
so they're editable on every source and must survive a sync round-trip.
"""

import os

import pytest

from bpm_tagger.db import BPMDatabase


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _app(base_config, **over):
    from bpm_tagger.config import build_config
    from bpm_tagger.web.app import create_app

    cfg = build_config()
    cfg.update({
        "db_path": base_config["db_path"],
        "music_dir": base_config["music_dir"],
        "ui_password": "s3cret",
        "ui_secret_key": "unit-test-secret-key",
        "write_tags": False,
    })
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


def _login(client, password="s3cret"):
    client.post("/api/login", json={"password": password})
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


# ── DB layer ─────────────────────────────────────────────────────────────────

def test_new_playlists_default_to_blank_description_unpinned(db):
    pid = db.add_local_playlist("Mix")
    pl = db.get_playlist(pid)
    assert pl["description"] == ""
    assert pl["pinned"] == 0


def test_update_playlist_meta_writes_only_what_it_is_given(db):
    pid = db.add_local_playlist("Mix")
    db.update_playlist_meta(pid, description="Long run fuel")
    pl = db.get_playlist(pid)
    assert pl["description"] == "Long run fuel"
    assert pl["name"] == "Mix"          # untouched
    assert pl["pinned"] == 0            # untouched

    db.update_playlist_meta(pid, name="Renamed", pinned=True)
    pl = db.get_playlist(pid)
    assert pl["name"] == "Renamed" and pl["pinned"] == 1
    assert pl["description"] == "Long run fuel"   # still untouched


def test_update_playlist_meta_with_nothing_is_a_noop(db):
    pid = db.add_local_playlist("Mix")
    db.update_playlist_meta(pid)
    assert db.get_playlist(pid)["name"] == "Mix"


def test_list_playlists_orders_pinned_first_then_alphabetically(db):
    db.add_local_playlist("Beta")
    db.add_local_playlist("Alpha")
    zed = db.add_local_playlist("Zed")
    db.update_playlist_meta(zed, pinned=True)

    assert [p["name"] for p in db.list_playlists()] == ["Zed", "Alpha", "Beta"]


def test_description_and_pinned_survive_a_sync(db):
    """Sync owns name/image/track_count — it must not clobber the user's fields."""
    db.add_playlist("SPX", "Spotify PL")
    pid = db.get_playlist_by_spotify_id("SPX")["id"]
    db.update_playlist_meta(pid, description="Tempo work", pinned=True)

    db.update_playlist_sync(pid, "snap2", "Renamed By Spotify", "http://img", 12)
    pl = db.get_playlist(pid)
    assert pl["name"] == "Renamed By Spotify"     # source still owns the name
    assert pl["description"] == "Tempo work"      # ours survived
    assert pl["pinned"] == 1

    db.mark_playlist_synced(pid, name="Again", track_count=13)
    pl = db.get_playlist(pid)
    assert pl["description"] == "Tempo work" and pl["pinned"] == 1


# ── API ──────────────────────────────────────────────────────────────────────

def test_patch_renames_a_local_playlist(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]

    r = client.patch(f"/api/playlists/{pid}", json={"name": "  Long Run  "}, headers=csrf)
    assert r.status_code == 200
    pl = r.get_json()["playlist"]
    assert pl["name"] == "Long Run"          # trimmed
    assert "have_count" in pl                # refreshed *with* counts


def test_patch_refuses_to_rename_a_synced_playlist(base_config):
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        pid = state().db.add_playlist("SPX", "Spotify PL")

    r = client.patch(f"/api/playlists/{pid}", json={"name": "Mine now"}, headers=csrf)
    assert r.status_code == 400
    with app.app_context():
        assert state().db.get_playlist(pid)["name"] == "Spotify PL"


@pytest.mark.parametrize("name", ["", "   ", None])
def test_patch_rejects_an_empty_name(base_config, name):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    assert client.patch(f"/api/playlists/{pid}", json={"name": name},
                        headers=csrf).status_code == 400


def test_patch_rejects_an_overlong_name(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    assert client.patch(f"/api/playlists/{pid}", json={"name": "x" * 201},
                        headers=csrf).status_code == 400
    assert client.patch(f"/api/playlists/{pid}", json={"name": "x" * 200},
                        headers=csrf).status_code == 200


def test_patch_sets_description_and_pinned_on_a_synced_playlist(base_config):
    """Unlike the name, these are ours to set on any source."""
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        pid = state().db.add_playlist("SPX", "Spotify PL")

    r = client.patch(f"/api/playlists/{pid}",
                     json={"description": " Threshold intervals ", "pinned": True},
                     headers=csrf)
    assert r.status_code == 200
    pl = r.get_json()["playlist"]
    assert pl["description"] == "Threshold intervals"
    assert pl["pinned"] == 1


def test_patch_rejects_an_overlong_description(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    assert client.patch(f"/api/playlists/{pid}", json={"description": "x" * 1001},
                        headers=csrf).status_code == 400


def test_patch_still_toggles_enabled(base_config):
    """The pre-existing behaviour of this endpoint is unchanged."""
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        pid = state().db.add_playlist("SPX", "Spotify PL")

    assert client.patch(f"/api/playlists/{pid}", json={"enabled": False},
                        headers=csrf).get_json()["playlist"]["enabled"] == 0
    assert client.patch(f"/api/playlists/{pid}", json={"enabled": True},
                        headers=csrf).get_json()["playlist"]["enabled"] == 1


def test_patch_combines_enabled_with_meta(base_config):
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        pid = state().db.add_playlist("SPX", "Spotify PL")

    pl = client.patch(f"/api/playlists/{pid}",
                      json={"enabled": False, "pinned": True, "description": "d"},
                      headers=csrf).get_json()["playlist"]
    assert pl["enabled"] == 0 and pl["pinned"] == 1 and pl["description"] == "d"


def test_patch_unknown_playlist_404s(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    assert client.patch("/api/playlists/9999", json={"pinned": True},
                        headers=csrf).status_code == 404


def test_patch_requires_csrf(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    assert client.patch(f"/api/playlists/{pid}", json={"pinned": True}).status_code == 403


def test_player_cannot_patch_a_playlist(base_config):
    """Playlist management stays outside the player allowlist."""
    client = _app(base_config, run_password="runner99").test_client()
    csrf = _login(client, "runner99")
    assert client.patch("/api/playlists/1", json={"pinned": True},
                        headers=csrf).status_code == 403


def test_api_listing_is_pinned_first(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    for name in ("Beta", "Alpha", "Zed"):
        client.post("/api/playlists", json={"source": "local", "name": name}, headers=csrf)
    pls = client.get("/api/playlists").get_json()["playlists"]
    zed = next(p for p in pls if p["name"] == "Zed")
    client.patch(f"/api/playlists/{zed['id']}", json={"pinned": True}, headers=csrf)

    names = [p["name"] for p in client.get("/api/playlists").get_json()["playlists"]]
    assert names == ["Zed", "Alpha", "Beta"]
