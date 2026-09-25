"""Subsonic API Phase 2: playlists, folder browsing, lyrics, similar/top songs,
and player-user accounts scoped to their playlists (docs/plans/subsonic-api.md)."""

import os
import sqlite3

import pytest

from test_subsonic import _app, _flac, _insert, _json, _login


def _playlist(db_path, name, paths, source="local"):
    conn = sqlite3.connect(db_path)
    pid = conn.execute("INSERT INTO playlists (source, name, created_at, enabled) VALUES (?, ?, "
                       "'2026-09-01T10:00:00+00:00', 1)", (source, name)).lastrowid
    for pos, p in enumerate(paths):
        conn.execute("INSERT INTO playlist_tracks (playlist_id, source_track_id, position, "
                     "match_status, matched_file_path, added_at) VALUES (?, ?, ?, 'have', ?, "
                     "'2026-09-02T10:00:00+00:00')", (pid, p, pos, p))
    conn.commit()
    conn.close()
    return pid


@pytest.fixture
def lib(base_config):
    """Admin + one player user; three tracks in two folders; two playlists."""
    app = _app(base_config)
    admin = app.test_client()
    csrf = _login(admin)
    db_path, music = base_config["db_path"], base_config["music_dir"]
    paths = {
        "run": os.path.join(music, "Pacer", "Cadence", "01 Run.flac"),
        "walk": os.path.join(music, "Pacer", "Cadence", "02 Walk.flac"),
        "other": os.path.join(music, "Stranger", "Elsewhere", "01 Other.flac"),
    }
    for p in paths.values():
        _flac(p)
    ids = {
        "run": _insert(db_path, paths["run"], title="Run", artist="Pacer", album="Cadence",
                       album_artist="Pacer", track_no=1, bpm=170.0, duration_ms=200000),
        "walk": _insert(db_path, paths["walk"], title="Walk", artist="Pacer", album="Cadence",
                        album_artist="Pacer", track_no=2, bpm=100.0, duration_ms=150000),
        "other": _insert(db_path, paths["other"], title="Other", artist="Stranger",
                         album="Elsewhere", album_artist="Stranger", track_no=1, bpm=86.0,
                         duration_ms=180000),
    }
    local = _playlist(db_path, "Mine", [paths["run"], paths["walk"]])
    synced = _playlist(db_path, "From Spotify", [paths["other"]], source="spotify")

    st = app.extensions["state"]
    player_id = st.db.add_player("jogger", "x", playlist_ids=[local])
    creds = {}
    for owner in ("admin", f"player:{player_id}"):
        creds[owner] = admin.post(f"/api/subsonic/accounts/{owner}/api-key",
                                  headers=csrf).get_json()["api_key"]
    client = app.test_client()

    def call(method, as_="admin", **params):
        key = creds["admin" if as_ == "admin" else f"player:{player_id}"]
        r = client.get(f"/rest/{method}", query_string={"apiKey": key, "f": "json", **params})
        return r

    return {"app": app, "st": st, "admin": admin, "csrf": csrf, "call": call, "paths": paths,
            "ids": ids, "local": local, "synced": synced, "player_id": player_id, "creds": creds}


def _body(r):
    return _json(r)


# ── Playlists ─────────────────────────────────────────────────────────────────

def test_get_playlists_and_entries(lib):
    pls = {p["name"]: p for p in _body(lib["call"]("getPlaylists"))["playlists"]["playlist"]
           if not p["id"].startswith("pl-run-")}  # Run presets: see test_subsonic_phase3
    assert set(pls) == {"Mine", "From Spotify"}
    assert pls["Mine"]["songCount"] == 2 and pls["Mine"]["readonly"] is False
    assert pls["From Spotify"]["readonly"] is True
    pl = _body(lib["call"]("getPlaylist", id=pls["Mine"]["id"]))["playlist"]
    assert [e["title"] for e in pl["entry"]] == ["Run", "Walk"]
    assert pl["duration"] == 350


def test_create_update_delete_local_playlist(lib):
    call = lib["call"]
    run, walk, other = (f"tr-{lib['ids'][k]}" for k in ("run", "walk", "other"))
    created = _body(lib["app"].test_client().get("/rest/createPlaylist", query_string=[
        ("apiKey", lib["creds"]["admin"]), ("f", "json"), ("name", "Tempo"),
        ("songId", run), ("songId", other)]))["playlist"]
    assert created["name"] == "Tempo" and [e["id"] for e in created["entry"]] == [run, other]

    _body(lib["app"].test_client().get("/rest/updatePlaylist", query_string=[
        ("apiKey", lib["creds"]["admin"]), ("f", "json"), ("playlistId", created["id"]),
        ("name", "Tempo 2"), ("comment", "fast"), ("songIndexToRemove", "0"), ("songIdToAdd", walk)]))
    pl = _body(call("getPlaylist", id=created["id"]))["playlist"]
    assert pl["name"] == "Tempo 2" and pl["comment"] == "fast"
    assert [e["id"] for e in pl["entry"]] == [other, walk]

    assert _body(call("deletePlaylist", id=created["id"]))["status"] == "ok"
    assert _body(call("getPlaylist", id=created["id"]))["error"]["code"] == 70


def test_create_with_playlist_id_replaces_songs(lib):
    other = f"tr-{lib['ids']['other']}"
    pl = _body(lib["call"]("createPlaylist", playlistId=f"pl-{lib['local']}", songId=other))["playlist"]
    assert [e["id"] for e in pl["entry"]] == [other]


def test_synced_playlists_are_read_only(lib):
    pid = f"pl-{lib['synced']}"
    for method, params in (("updatePlaylist", {"playlistId": pid, "name": "x"}),
                           ("deletePlaylist", {"id": pid}),
                           ("createPlaylist", {"playlistId": pid})):
        assert _body(lib["call"](method, **params))["error"]["code"] == 50, method


# ── Folder browsing ───────────────────────────────────────────────────────────

def test_indexes_and_music_directory_walk(lib):
    call = lib["call"]
    idx = _body(call("getIndexes"))["indexes"]["index"]
    top = {a["name"]: a["id"] for i in idx for a in i["artist"]}
    assert set(top) == {"Pacer", "Stranger"}

    pacer = _body(call("getMusicDirectory", id=top["Pacer"]))["directory"]
    assert pacer["name"] == "Pacer" and [c["title"] for c in pacer["child"]] == ["Cadence"]
    album_dir = pacer["child"][0]
    assert album_dir["isDir"] is True and album_dir["parent"] == top["Pacer"]

    cadence = _body(call("getMusicDirectory", id=album_dir["id"]))["directory"]
    assert [c["title"] for c in cadence["child"]] == ["Run", "Walk"]
    assert cadence["parent"] == top["Pacer"]
    # a song's parent is its folder, so folder clients can navigate back up
    assert cadence["child"][0]["parent"] == album_dir["id"]


def test_music_directory_accepts_album_and_artist_ids(lib):
    song = _body(lib["call"]("getSong", id=f"tr-{lib['ids']['run']}"))["song"]
    d = _body(lib["call"]("getMusicDirectory", id=song["albumId"]))["directory"]
    assert [c["title"] for c in d["child"]] == ["Run", "Walk"]
    d = _body(lib["call"]("getMusicDirectory", id=song["artistId"]))["directory"]
    assert [c["name"] for c in d["child"]] == ["Cadence"]


def test_unknown_directory_is_70(lib):
    assert _body(lib["call"]("getMusicDirectory", id="dir-0000"))["error"]["code"] == 70


# ── Lyrics ────────────────────────────────────────────────────────────────────

def test_synced_lyrics_from_sidecar(lib):
    with open(os.path.splitext(lib["paths"]["run"])[0] + ".lrc", "w", encoding="utf-8") as f:
        f.write("[ar:Pacer]\n[00:01.50]First line\n[00:03.00][00:10.25]Chorus\n")
    res = _body(lib["call"]("getLyricsBySongId", id=f"tr-{lib['ids']['run']}"))
    lyr = res["lyricsList"]["structuredLyrics"][0]
    assert lyr["synced"] is True
    assert [(ln["start"], ln["value"]) for ln in lyr["line"]] == [
        (1500, "First line"), (3000, "Chorus"), (10250, "Chorus")]

    legacy = _body(lib["call"]("getLyrics", artist="Pacer", title="Run"))["lyrics"]
    assert legacy["value"] == "First line\nChorus\nChorus"


def test_plain_lyrics_and_none(lib):
    with open(os.path.splitext(lib["paths"]["walk"])[0] + ".lrc", "w", encoding="utf-8") as f:
        f.write("Just words\nMore words")
    lyr = _body(lib["call"]("getLyricsBySongId", id=f"tr-{lib['ids']['walk']}")
                )["lyricsList"]["structuredLyrics"][0]
    assert lyr["synced"] is False and [ln["value"] for ln in lyr["line"]] == ["Just words", "More words"]
    none = _body(lib["call"]("getLyricsBySongId", id=f"tr-{lib['ids']['other']}"))
    assert none["lyricsList"]["structuredLyrics"] == []


def test_lyrics_extension_advertised(lib):
    names = {e["name"] for e in _body(lib["call"]("getOpenSubsonicExtensions"))["openSubsonicExtensions"]}
    assert "songLyrics" in names


# ── Similar / top ─────────────────────────────────────────────────────────────

def test_similar_songs_prefers_artist_then_folded_tempo(lib):
    songs = _body(lib["call"]("getSimilarSongs", id=f"tr-{lib['ids']['run']}", count=5)
                  )["similarSongs"]["song"]
    titles = [s["title"] for s in songs]
    # Walk: same artist. Other: 86 BPM folds to 172, within 5 % of 170.
    assert titles[0] == "Walk" and "Other" in titles and "Run" not in titles


def test_top_songs_by_play_count(lib):
    lib["call"]("scrobble", id=f"tr-{lib['ids']['walk']}")
    top = _body(lib["call"]("getTopSongs", artist="Pacer"))["topSongs"]["song"]
    assert [s["title"] for s in top] == ["Walk", "Run"]


# ── Player users ──────────────────────────────────────────────────────────────

def test_player_sees_only_its_playlists_tracks(lib):
    call = lambda m, **p: _body(lib["call"](m, as_="player", **p))  # noqa: E731
    assert call("getUser")["user"]["username"] == "jogger"
    assert call("getUser")["user"]["playlistRole"] is False
    artists = [a["name"] for i in call("getArtists")["artists"]["index"] for a in i["artist"]]
    assert artists == ["Pacer"]
    assert [p["name"] for p in call("getPlaylists")["playlists"]["playlist"]
            if not p["id"].startswith("pl-run-")] == ["Mine"]
    songs = call("search3", query="")["searchResult3"]["song"]
    assert sorted(s["title"] for s in songs) == ["Run", "Walk"]
    top = [a["name"] for i in call("getIndexes")["indexes"]["index"] for a in i["artist"]]
    assert top == ["Pacer"]


def test_player_cannot_reach_tracks_outside_scope(lib):
    call = lambda m, **p: lib["call"](m, as_="player", **p)  # noqa: E731
    other = f"tr-{lib['ids']['other']}"
    for method in ("getSong", "stream", "star", "scrobble"):
        assert _body(call(method, id=other))["error"]["code"] == 70, method
    assert _body(call("getPlaylist", id=f"pl-{lib['synced']}"))["error"]["code"] == 70


def test_player_cannot_edit_playlists(lib):
    r = _body(lib["call"]("createPlaylist", as_="player", name="nope"))
    assert r["error"]["code"] == 50


def test_player_stars_and_scrobbles_are_attributed(lib):
    run = f"tr-{lib['ids']['run']}"
    lib["call"]("star", as_="player", id=run)
    lib["call"]("scrobble", as_="player", id=run)
    assert lib["st"].db.get_track(lib["paths"]["run"])["starred"] == 1
    events = lib["st"].db.list_play_events()
    assert events[0]["owner"] == f"player:{lib['player_id']}"


def test_disabled_player_is_locked_out_immediately(lib):
    lib["st"].db.update_player(lib["player_id"], enabled=False)
    assert _body(lib["call"]("ping", as_="player"))["error"]["code"] == 50


def test_deleting_a_player_drops_its_credentials(lib):
    lib["admin"].delete(f"/api/players/{lib['player_id']}", headers=lib["csrf"])
    assert lib["st"].db.get_subsonic_credentials(f"player:{lib['player_id']}") is None
    assert _body(lib["call"]("ping", as_="player"))["error"]["code"] == 44


def test_player_token_auth_with_its_own_password(lib):
    import hashlib
    owner = f"player:{lib['player_id']}"
    pw = lib["admin"].post(f"/api/subsonic/accounts/{owner}/password",
                           headers=lib["csrf"]).get_json()["password"]
    token = hashlib.md5((pw + "salt").encode()).hexdigest()
    r = lib["app"].test_client().get("/rest/ping", query_string={
        "u": "jogger", "t": token, "s": "salt", "f": "json"})
    assert _body(r)["status"] == "ok"


def test_admin_status_lists_player_accounts(lib):
    accounts = lib["admin"].get("/api/subsonic").get_json()["accounts"]
    assert [(a["username"], a["kind"], a["has_api_key"]) for a in accounts] == [
        ("admin", "admin", True), ("jogger", "player", True)]


def test_credentials_for_unknown_account_404(lib):
    r = lib["admin"].post("/api/subsonic/accounts/player:999/api-key", headers=lib["csrf"])
    assert r.status_code == 404
