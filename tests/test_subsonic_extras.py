"""Subsonic genres, album/artist stars, and the album index
(docs/plans/subsonic-api.md, follow-ups after Phase 3)."""

import os
import sqlite3

import pytest

from test_subsonic import _app, _flac, _insert, _json, _login


@pytest.fixture(autouse=True)
def _rebuild_every_read(monkeypatch):
    # Production throttles rebuilds; these tests check freshness read by read.
    monkeypatch.setattr("bpm_tagger.db.subsonic.ALBUM_INDEX_MIN_INTERVAL", 0.0)


def test_rebuilds_are_throttled(env, monkeypatch):
    monkeypatch.setattr("bpm_tagger.db.subsonic.ALBUM_INDEX_MIN_INTERVAL", 3600.0)
    db = env["st"].db
    db.refresh_album_index(force=True)
    env["track"]("z1", "Zed", "Late", "Pop")
    assert db.refresh_album_index() is False          # inside the interval: stale is served
    assert db.album_index_dirty() is True             # …but the change is remembered
    assert db.refresh_album_index(force=True) is True


@pytest.fixture
def env(base_config):
    app = _app(base_config)
    admin = app.test_client()
    csrf = _login(admin)
    key = admin.post("/api/subsonic/accounts/admin/api-key", headers=csrf).get_json()["api_key"]
    db_path, music = base_config["db_path"], base_config["music_dir"]
    st = app.extensions["state"]
    ids = {}

    def track(name, artist, album, genre, **kw):
        path = os.path.join(music, artist, album, f"{name}.flac")
        _flac(path)
        tid = _insert(db_path, path, title=name, artist=artist, album=album,
                      album_artist=artist, genre=genre, duration_ms=60000, **kw)
        # The tag index maintains track_genres; tests insert rows directly.
        st.db.update_track_tags(path, {**st.db.get_track(path), "genre": genre},
                                st.db.get_track(path)["file_hash"] or "")
        ids[name] = tid
        return path

    track("a1", "Ann", "First", "House; Techno", year=2020, bpm=124.0)
    track("a2", "Ann", "First", "House", year=2020, bpm=126.0)
    track("b1", "Ben", "Second", "Ambient", year=2018, bpm=80.0)
    client = app.test_client()

    def call(method, key_=None, **params):
        return _json(client.get(f"/rest/{method}",
                                query_string={"apiKey": key_ or key, "f": "json", **params}))
    return {"app": app, "st": st, "admin": admin, "csrf": csrf, "call": call, "ids": ids,
            "track": track, "db_path": db_path}


def _album_id(env, title):
    return env["call"]("getSong", id=f"tr-{env['ids'][title]}")["song"]["albumId"]


# ── Genres ────────────────────────────────────────────────────────────────────

def test_get_genres_counts(env):
    genres = {g["value"]: (g["songCount"], g["albumCount"])
              for g in env["call"]("getGenres")["genres"]["genre"]}
    assert genres == {"Ambient": (1, 1), "House": (2, 1), "Techno": (1, 1)}


def test_song_carries_genre_and_genres(env):
    song = env["call"]("getSong", id=f"tr-{env['ids']['a1']}")["song"]
    assert song["genre"] == "House"
    assert song["genres"] == [{"name": "House"}, {"name": "Techno"}]


def test_songs_by_genre_is_case_insensitive(env):
    songs = env["call"]("getSongsByGenre", genre="house")["songsByGenre"]["song"]
    assert sorted(s["title"] for s in songs) == ["a1", "a2"]


def test_album_list_by_genre(env):
    albums = env["call"]("getAlbumList2", type="byGenre", genre="Ambient")["albumList2"]["album"]
    assert [a["name"] for a in albums] == ["Second"]
    assert albums[0]["genre"] == "Ambient"


def test_random_songs_genre_filter(env):
    with sqlite3.connect(env["db_path"]) as conn:  # random songs need analyzed tracks
        conn.execute("UPDATE tracks SET status = 'done'")
    songs = env["call"]("getRandomSongs", size=10, genre="Techno")["randomSongs"]["song"]
    assert [s["title"] for s in songs] == ["a1"]


def test_genres_xml_uses_text_content(env):
    import xml.etree.ElementTree as ET
    client = env["app"].test_client()
    key = env["admin"].post("/api/subsonic/accounts/admin/api-key",
                            headers=env["csrf"]).get_json()["api_key"]
    root = ET.fromstring(client.get("/rest/getGenres", query_string={"apiKey": key}).data)
    ns = "{http://subsonic.org/restapi}"
    first = root.find(f"{ns}genres/{ns}genre")
    assert first.text == "Ambient" and first.get("songCount") == "1"


# ── Album / artist stars ──────────────────────────────────────────────────────

def test_star_album_and_artist(env):
    aid = _album_id(env, "a1")
    art = env["call"]("getSong", id=f"tr-{env['ids']['b1']}")["song"]["artistId"]
    assert env["call"]("star", albumId=aid, artistId=art)["status"] == "ok"

    starred = env["call"]("getStarred2")["starred2"]
    assert [a["id"] for a in starred["album"]] == [aid] and starred["album"][0]["starred"]
    assert [a["id"] for a in starred["artist"]] == [art]
    assert env["call"]("getAlbum", id=aid)["album"]["starred"]
    assert env["call"]("getArtist", id=art)["artist"]["starred"]
    listed = env["call"]("getAlbumList2", type="starred")["albumList2"]["album"]
    assert [a["id"] for a in listed] == [aid]

    env["call"]("unstar", albumId=aid, artistId=art)
    starred = env["call"]("getStarred2")["starred2"]
    assert starred["album"] == [] and starred["artist"] == []
    assert "starred" not in env["call"]("getAlbum", id=aid)["album"]


def test_star_unknown_album_is_70(env):
    assert env["call"]("star", albumId="al-0000")["error"]["code"] == 70


def test_player_cannot_star_an_album_outside_scope(env):
    st = env["st"]
    player = st.db.add_player("p", "x", playlist_ids=[])
    key = env["admin"].post(f"/api/subsonic/accounts/player:{player}/api-key",
                            headers=env["csrf"]).get_json()["api_key"]
    assert env["call"]("star", key_=key, albumId=_album_id(env, "a1"))["error"]["code"] == 70


# ── Album index ───────────────────────────────────────────────────────────────

def _triggers(db_path):
    with sqlite3.connect(db_path) as conn:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'sub_albums_%'")}


def test_triggers_follow_the_toggle(base_config):
    _app(base_config, subsonic_enabled=True)
    assert _triggers(base_config["db_path"]) == {"sub_albums_ins", "sub_albums_del", "sub_albums_upd"}
    _app(base_config, subsonic_enabled=False)
    assert _triggers(base_config["db_path"]) == set()


def test_index_tracks_library_changes(env):
    db = env["st"].db
    names = lambda: [a["name"] for a in env["call"](  # noqa: E731
        "getAlbumList2", type="alphabeticalByName", size=50)["albumList2"]["album"]]
    assert names() == ["First", "Second"]
    assert db.album_index_dirty() is False  # the read rebuilt it

    env["track"]("c1", "Cat", "Third", "Jazz", year=2024)
    assert db.album_index_dirty() is True
    assert names() == ["First", "Second", "Third"]

    with sqlite3.connect(env["db_path"]) as conn:
        conn.execute("UPDATE tracks SET play_count = 9 WHERE title = 'b1'")
    frequent = env["call"]("getAlbumList2", type="frequent")["albumList2"]["album"]
    assert frequent[0]["name"] == "Second" and frequent[0]["playCount"] == 9

    with sqlite3.connect(env["db_path"]) as conn:
        conn.execute("UPDATE tracks SET status = 'deleted' WHERE title = 'c1'")
    assert names() == ["First", "Second"]


def test_unrelated_updates_leave_the_index_clean(env):
    db = env["st"].db
    env["call"]("getAlbumList2", type="newest")
    with sqlite3.connect(env["db_path"]) as conn:
        conn.execute("UPDATE tracks SET waveform_peaks = '[]', lyrics_status = 'none'")
    assert db.album_index_dirty() is False


def test_index_aggregates_match_the_live_query(env):
    """The precomputed index (admin) and the on-the-fly aggregate (scoped
    callers) must agree: scope a playlist holding every track and compare."""
    db = env["st"].db
    pid = db.add_local_playlist("all")
    db.add_tracks_to_local_playlist(pid, db.subsonic_all_paths())
    cols = ("name", "album_artist", "artist", "song_count", "duration_ms", "year",
            "created", "play_count", "last_played", "genre")
    pick = lambda rows: [{c: r[c] for c in cols} for r in rows]  # noqa: E731
    indexed = db.subsonic_albums("alphabeticalByName", limit=50)
    live = db.subsonic_albums("alphabeticalByName", limit=50, scope=[pid])
    assert pick(indexed) == pick(live)
    assert [(r["name"], r["song_count"], r["genre"]) for r in indexed] == [
        ("First", 2, "House"), ("Second", 1, "Ambient")]


def test_album_ids_resolve_through_the_index(env):
    aid = _album_id(env, "b1")
    assert env["st"].db.subsonic_album_id_map()[aid] == [("Second", "Ben")]
    assert env["call"]("getAlbum", id=aid)["album"]["name"] == "Second"
