"""Local playlist cover art: a custom pick, else an auto-collage of the tracks.

Neither layer writes ``playlists.image_url`` — that column stays whatever the
synced source reported, so a Spotify/Navidrome mirror keeps rendering its own CDN
art and a sync can never clobber a cover the user chose. Both are Local-only for
the same reason renaming is.
"""

import os
import sqlite3

import pytest

import bpm_tagger.web.api.playlists as pl_mod


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


def _seed_track(base_config, name, album="Alb"):
    """A library track backed by a real (empty) file, so os.stat in the collage
    key works. Its 'cover art' is supplied by monkeypatching read_cover."""
    path = os.path.join(base_config["music_dir"], f"{name}.mp3")
    with open(path, "wb") as f:
        f.write(b"\x00")
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, album, bpm, status) "
        "VALUES (?, ?, 'Artist', ?, 150, 'done')", (path, name, album))
    conn.commit()
    conn.close()
    return path


def _local_with_tracks(client, csrf, base_config, specs):
    """Create a local playlist holding the given [(name, album)] tracks."""
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    paths = []
    for name, album in specs:
        p = _seed_track(base_config, name, album)
        client.post(f"/api/playlists/{pid}/tracks", json={"path": p}, headers=csrf)
        paths.append(p)
    return pid, paths


@pytest.fixture(autouse=True)
def _clear_collage_cache():
    pl_mod._collage_cache.clear()
    yield
    pl_mod._collage_cache.clear()


# A 1x1 JPEG — small but a real image, so Pillow can actually compose with it.
_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffc2000b080001000101011100ffc4001400010"
    "0000000000000000000000000000009ffda0008010100000000013fffc4001410010000000"
    "0000000000000000000000000ffda0008010100010500ffd9")


# ── custom cover ─────────────────────────────────────────────────────────────

def test_upload_then_serve_with_an_etag(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]

    up = client.put(f"/api/playlists/{pid}/cover", data=_JPEG,
                    content_type="application/octet-stream", headers=csrf)
    assert up.status_code == 200 and up.get_json()["ok"] is True

    got = client.get(f"/api/playlists/{pid}/cover")
    assert got.status_code == 200
    assert got.mimetype == "image/jpeg"
    assert got.data
    assert got.headers.get("ETag")


def test_custom_cover_does_not_touch_image_url(base_config):
    """The column belongs to the synced sources — local covers are files."""
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    client.put(f"/api/playlists/{pid}/cover", data=_JPEG,
               content_type="application/octet-stream", headers=csrf)

    from bpm_tagger.web.state import state
    with app.app_context():
        assert not state().db.get_playlist(pid)["image_url"]


def test_url_mode_refuses_a_private_host(base_config, monkeypatch):
    """The SSRF guard on the shared image helper applies here too."""
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("192.168.1.10", 80))])
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]

    r = client.post(f"/api/playlists/{pid}/cover",
                    json={"url": "http://internal.example/cover.jpg"}, headers=csrf)
    assert r.status_code == 400
    assert client.get(f"/api/playlists/{pid}/cover").status_code == 404


def test_set_cover_rejects_a_synced_playlist(base_config):
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        pid = state().db.add_playlist("SPX", "Spotify PL")

    r = client.put(f"/api/playlists/{pid}/cover", data=_JPEG,
                   content_type="application/octet-stream", headers=csrf)
    assert r.status_code == 400


def test_get_cover_404s_for_a_synced_playlist(base_config):
    """They render their source's image_url client-side instead."""
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        pid = state().db.add_playlist("SPX", "Spotify PL")
    assert client.get(f"/api/playlists/{pid}/cover").status_code == 404


def test_set_cover_404s_for_an_unknown_playlist(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    assert client.put("/api/playlists/9999/cover", data=_JPEG,
                      content_type="application/octet-stream",
                      headers=csrf).status_code == 404


def test_cover_endpoints_need_csrf_and_block_players(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    # No CSRF header on a state-changing call.
    assert client.put(f"/api/playlists/{pid}/cover", data=_JPEG,
                      content_type="application/octet-stream").status_code == 403

    player = _app(base_config, run_password="runner99").test_client()
    pcsrf = _login(player, "runner99")
    # Not in the player allowlist — even the read is admin/guest-only.
    assert player.get(f"/api/playlists/{pid}/cover").status_code == 403
    assert player.delete(f"/api/playlists/{pid}/cover", headers=pcsrf).status_code == 403


def test_delete_falls_back_to_the_collage(base_config, monkeypatch):
    monkeypatch.setattr(pl_mod, "read_cover", lambda p: (_JPEG, "image/jpeg"))
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid, _ = _local_with_tracks(client, csrf, base_config, [("a", "A")])

    client.put(f"/api/playlists/{pid}/cover", data=_JPEG,
               content_type="application/octet-stream", headers=csrf)
    assert client.get(f"/api/playlists/{pid}/cover").status_code == 200

    d = client.delete(f"/api/playlists/{pid}/cover", headers=csrf)
    assert d.status_code == 200 and d.get_json()["removed"] is True
    # Still 200 — but now it's the collage path, not the file.
    assert client.get(f"/api/playlists/{pid}/cover").status_code == 200


def test_delete_then_get_404s_when_there_is_nothing_to_collage(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    client.put(f"/api/playlists/{pid}/cover", data=_JPEG,
               content_type="application/octet-stream", headers=csrf)
    client.delete(f"/api/playlists/{pid}/cover", headers=csrf)
    assert client.get(f"/api/playlists/{pid}/cover").status_code == 404


# ── auto-collage ─────────────────────────────────────────────────────────────

def test_empty_playlist_has_no_cover(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    assert client.get(f"/api/playlists/{pid}/cover").status_code == 404


def test_tracks_without_art_have_no_cover(base_config, monkeypatch):
    monkeypatch.setattr(pl_mod, "read_cover", lambda p: None)
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid, _ = _local_with_tracks(client, csrf, base_config, [("a", "A"), ("b", "B")])
    assert client.get(f"/api/playlists/{pid}/cover").status_code == 404


def test_two_covers_serve_the_first_one_alone(base_config, monkeypatch):
    """Fewer than four tiles is not a grid — serve one cover rather than a
    lopsided composition."""
    covers = {"a": _JPEG + b"A", "b": _JPEG + b"B"}
    monkeypatch.setattr(pl_mod, "read_cover",
                        lambda p: (covers[os.path.basename(p)[0]], "image/jpeg"))
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid, _ = _local_with_tracks(client, csrf, base_config, [("a", "A"), ("b", "B")])

    r = client.get(f"/api/playlists/{pid}/cover")
    assert r.status_code == 200
    assert r.data == covers["a"]


def test_four_covers_compose_a_grid_when_pillow_is_present(base_config, monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setattr(pl_mod, "read_cover", lambda p: (_JPEG, "image/jpeg"))
    client = _app(base_config).test_client()
    csrf = _login(client)
    # Distinct albums so the album dedupe doesn't collapse them...
    pid, _ = _local_with_tracks(client, csrf, base_config,
                                [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")])
    # ...but the cover-bytes dedupe would, so give each its own bytes.
    seq = {"a": _JPEG, "b": _JPEG + b"B", "c": _JPEG + b"C", "d": _JPEG + b"D"}
    monkeypatch.setattr(pl_mod, "read_cover",
                        lambda p: (seq[os.path.basename(p)[0]], "image/jpeg"))

    r = client.get(f"/api/playlists/{pid}/cover")
    assert r.status_code == 200
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(r.data))
    assert img.size == (pl_mod._COLLAGE_PX, pl_mod._COLLAGE_PX)


def test_falls_back_to_one_cover_when_pillow_is_missing(base_config, monkeypatch):
    """The slim image ships without Pillow — degrade, don't 500."""
    seq = {"a": _JPEG, "b": _JPEG + b"B", "c": _JPEG + b"C", "d": _JPEG + b"D"}
    monkeypatch.setattr(pl_mod, "read_cover",
                        lambda p: (seq[os.path.basename(p)[0]], "image/jpeg"))
    monkeypatch.setattr(pl_mod, "_compose_collage", lambda sources: None)
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid, _ = _local_with_tracks(client, csrf, base_config,
                                [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")])

    r = client.get(f"/api/playlists/{pid}/cover")
    assert r.status_code == 200 and r.data == _JPEG


def test_one_album_cannot_fill_the_grid(base_config, monkeypatch):
    """Four tracks off the same album would make a grid of one repeated cover."""
    seq = {c: _JPEG + c.encode() for c in "abcd"}
    monkeypatch.setattr(pl_mod, "read_cover",
                        lambda p: (seq[os.path.basename(p)[0]], "image/jpeg"))
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid, _ = _local_with_tracks(client, csrf, base_config,
                                [(c, "Same Album") for c in "abcd"])

    with _app(base_config).app_context():
        assert len(pl_mod._collage_sources(pid)) == 1


def test_membership_change_changes_the_etag(base_config, monkeypatch):
    seq = {c: _JPEG + c.encode() for c in "abcd"}
    monkeypatch.setattr(pl_mod, "read_cover",
                        lambda p: (seq[os.path.basename(p)[0]], "image/jpeg"))
    client = _app(base_config).test_client()
    csrf = _login(client)
    pid, _ = _local_with_tracks(client, csrf, base_config, [("a", "A"), ("b", "B")])

    first = client.get(f"/api/playlists/{pid}/cover")
    etag = first.headers["ETag"]
    # A repeat with the same membership is a cache hit.
    assert client.get(f"/api/playlists/{pid}/cover",
                      headers={"If-None-Match": etag}).status_code == 304

    # Adding a track ahead of the others changes the source set → new identity.
    c = _seed_track(base_config, "c", "C")
    client.post(f"/api/playlists/{pid}/tracks", json={"path": c}, headers=csrf)
    second = client.get(f"/api/playlists/{pid}/cover")
    assert second.status_code == 200
    assert second.headers["ETag"] != etag
