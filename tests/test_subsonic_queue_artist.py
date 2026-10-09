"""Subsonic play queue (savePlayQueue / getPlayQueue + OpenSubsonic index-based
pair) and getArtistInfo2 (biography, photo URLs, in-library similar artists)."""

import importlib
import os
import sqlite3

import pytest

from test_subsonic import _app, _flac, _insert, _json, _login

ai = importlib.import_module("bpm_tagger.web.subsonic.artist_info")


@pytest.fixture
def env(base_config, monkeypatch):
    from bpm_tagger.web.api import suggestions
    monkeypatch.setattr(suggestions, "_CACHE", {})

    def make(**over):
        app = _app(base_config, **over)
        admin = app.test_client()
        csrf = _login(admin)
        key = admin.post("/api/subsonic/accounts/admin/api-key", headers=csrf).get_json()["api_key"]
        ids = {}
        for name, artist in (("a", "Ann"), ("b", "Ann"), ("c", "Ben"), ("d", "Cat")):
            path = os.path.join(base_config["music_dir"], artist, f"{name}.flac")
            _flac(path)
            ids[name] = _insert(base_config["db_path"], path, title=name, artist=artist,
                                album=f"{artist} LP", album_artist=artist, duration_ms=60000)
        client = app.test_client()

        def call(method, key_=None, params=None, **kw):
            q = [("apiKey", key_ or key), ("f", "json"), ("c", "TestApp")]
            q += list((params or {}).items()) + list(kw.items())
            return _json(client.get(f"/rest/{method}", query_string=q))
        return {"app": app, "admin": admin, "csrf": csrf, "call": call, "ids": ids,
                "client": client, "key": key,
                "db": app.extensions["state"].db, "music": base_config["music_dir"],
                "db_path": base_config["db_path"]}
    return make


def _tr(e, *names):
    return [f"tr-{e['ids'][n]}" for n in names]


# ── Play queue ────────────────────────────────────────────────────────────────

def test_save_and_get_play_queue(env):
    e = env()
    a, b, c = _tr(e, "a", "b", "c")
    client, key = _save(e, "savePlayQueue", [("c", "Feishin"), ("id", a), ("id", b), ("id", c),
                                             ("current", b), ("position", "42000")])
    pq = _json(client.get("/rest/getPlayQueue", query_string={"apiKey": key, "f": "json"}))["playQueue"]
    assert [s["id"] for s in pq["entry"]] == [a, b, c]
    assert pq["current"] == b and pq["position"] == 42000
    assert pq["username"] == "admin" and pq["changedBy"] == "Feishin" and pq["changed"]


def _save(e, method, items):
    # One key per test: generating a new one replaces (revokes) the old.
    client, key = e["client"], e["key"]
    r = client.get(f"/rest/{method}", query_string=[("apiKey", key), ("f", "json"), *items])
    assert r.get_json()["subsonic-response"]["status"] == "ok"
    return client, key


def test_index_based_queue_handles_duplicates(env):
    e = env()
    a, b = _tr(e, "a", "b")
    client, key = _save(e, "savePlayQueueByIndex",
                        [("id", a), ("id", b), ("id", a), ("currentIndex", "2"), ("position", "5")])
    pq = _json(client.get("/rest/getPlayQueueByIndex",
                          query_string={"apiKey": key, "f": "json"}))["playQueueByIndex"]
    assert [s["id"] for s in pq["entry"]] == [a, b, a] and pq["currentIndex"] == 2
    # The id-based view of the same queue names the song, not the position.
    assert _json(client.get("/rest/getPlayQueue",
                            query_string={"apiKey": key, "f": "json"}))["playQueue"]["current"] == a


def test_no_ids_clears_the_queue(env):
    e = env()
    client, key = _save(e, "savePlayQueue", [("id", _tr(e, "a")[0])])
    _save(e, "savePlayQueue", [])
    body = _json(client.get("/rest/getPlayQueue", query_string={"apiKey": key, "f": "json"}))
    assert body["status"] == "ok" and "playQueue" not in body


def test_deleted_songs_drop_out_and_current_moves_on(env):
    e = env()
    a, b, c = _tr(e, "a", "b", "c")
    client, key = _save(e, "savePlayQueue", [("id", a), ("id", b), ("id", c), ("current", b)])
    with sqlite3.connect(e["db_path"]) as conn:
        conn.execute("UPDATE tracks SET status = 'deleted' WHERE id = ?", (e["ids"]["b"],))
    pq = _json(client.get("/rest/getPlayQueue", query_string={"apiKey": key, "f": "json"}))["playQueue"]
    assert [s["id"] for s in pq["entry"]] == [a, c] and pq["current"] == c


def test_queues_are_per_account_and_scoped(env):
    e = env()
    db = e["db"]
    pid = db.add_local_playlist("p")
    db.add_tracks_to_local_playlist(pid, [db.get_track_by_id(e["ids"]["a"])["file_path"]])
    player = db.add_player("jog", "x", playlist_ids=[pid])
    pkey = e["admin"].post(f"/api/subsonic/accounts/player:{player}/api-key",
                           headers=e["csrf"]).get_json()["api_key"]
    _save(e, "savePlayQueue", [("id", _tr(e, "c")[0])])         # the admin's queue
    client = e["app"].test_client()
    q = lambda: _json(client.get("/rest/getPlayQueue",  # noqa: E731
                                 query_string={"apiKey": pkey, "f": "json"}))
    assert "playQueue" not in q()                                   # not the admin's
    a, c = _tr(e, "a", "c")
    client.get("/rest/savePlayQueue", query_string=[("apiKey", pkey), ("id", a), ("id", c)])
    assert [s["id"] for s in q()["playQueue"]["entry"]] == [a]      # out-of-scope c dropped


def test_index_based_queue_is_advertised(env):
    e = env()
    names = {x["name"] for x in e["call"]("getOpenSubsonicExtensions")["openSubsonicExtensions"]}
    assert "indexBasedQueue" in names


# ── Artist info ───────────────────────────────────────────────────────────────

@pytest.fixture
def online(monkeypatch):
    calls = []
    from bpm_tagger.integrations import artist_info as bio_mod
    from bpm_tagger.integrations import deezer_catalog as dz
    monkeypatch.setattr(dz, "search_artist", lambda n: calls.append(("search", n)) or {
        "dz_id": "7", "name": n,
        "image_url": "https://cdn-images.dzcdn.net/images/artist/abc/1000x1000-000000-80-0-0.jpg"})
    monkeypatch.setattr(dz, "related_artists", lambda i: calls.append(("related", i)) or [
        {"dz_id": "8", "name": "Ben", "image_url": ""},
        {"dz_id": "9", "name": "Nobody Here", "image_url": ""},
        {"dz_id": "10", "name": "Cat", "image_url": ""}])
    monkeypatch.setattr(bio_mod, "artist_bio", lambda n: calls.append(("bio", n)) or f"{n} is a band.")
    return calls


def _ann(e):
    return e["call"]("getSong", id=_tr(e, "a")[0])["song"]["artistId"]


def test_artist_info_bio_and_in_library_similar(env, online):
    e = env()
    info = e["call"]("getArtistInfo2", id=_ann(e))["artistInfo2"]
    assert info["biography"] == "Ann is a band."
    assert [a["name"] for a in info["similarArtist"]] == ["Ben", "Cat"]   # "Nobody Here" isn't ours
    assert "largeImageUrl" not in info                                   # fetch_artist_images off
    # Cached: a second visit makes no network calls.
    n = len(online)
    e["call"]("getArtistInfo2", id=_ann(e))
    assert len(online) == n


def test_artist_photo_urls_need_the_images_opt_in(env, online):
    e = env(fetch_artist_images=True)
    info = e["call"]("getArtistInfo2", id=_ann(e))["artistInfo2"]
    assert info["smallImageUrl"].endswith("/250x250-000000-80-0-0.jpg")
    assert info["largeImageUrl"].endswith("/1000x1000-000000-80-0-0.jpg")


def test_count_caps_similar_artists(env, online):
    e = env()
    assert len(e["call"]("getArtistInfo2", id=_ann(e), count=1)["artistInfo2"]["similarArtist"]) == 1


def test_artist_info_can_be_turned_off(env, online):
    e = env(subsonic_artist_info=False)
    info = e["call"]("getArtistInfo2", id=_ann(e))["artistInfo2"]
    assert info == {"similarArtist": []} and online == []


def test_slow_lookup_answers_with_what_is_cached(env, monkeypatch):
    import time
    from bpm_tagger.integrations import artist_info as bio_mod
    from bpm_tagger.integrations import deezer_catalog as dz
    monkeypatch.setattr(ai, "WAIT_S", 0.05)
    monkeypatch.setattr(dz, "search_artist", lambda n: {"dz_id": "", "name": n, "image_url": ""})
    monkeypatch.setattr(bio_mod, "artist_bio", lambda n: time.sleep(0.4) or "Late bio.")
    e = env()
    assert "biography" not in e["call"]("getArtistInfo2", id=_ann(e))["artistInfo2"]
    for _ in range(40):
        if ai.cached("Ann")["bio"]:
            break
        time.sleep(0.05)
    assert e["call"]("getArtistInfo2", id=_ann(e))["artistInfo2"]["biography"] == "Late bio."


def test_artist_cover_prefers_the_local_artist_photo(env):
    from PIL import Image
    e = env()
    Image.new("RGB", (800, 800), (10, 200, 30)).save(os.path.join(e["music"], "Ann", "artist.jpg"))
    client, key = e["client"], e["key"]
    r = client.get("/rest/getCoverArt", query_string={"apiKey": key, "id": _ann(e), "size": 200})
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    import io
    img = Image.open(io.BytesIO(r.data))
    assert max(img.size) <= 200 and img.getpixel((5, 5))[1] > 150   # the green artist photo
