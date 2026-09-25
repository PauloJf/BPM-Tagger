"""Subsonic API Phase 4: per-account ratings — setRating, userRating on every
song, star/unstar through the derived star, and rating-weighted
getRandomSongs / getSimilarSongs(2) / Run playlists that skip the caller's own
dislikes (docs/plans/ratings-weighted-picking.md, docs/subsonic-api.md)."""

import os

import pytest

from test_subsonic import _app, _flac, _insert, _json, _login
from test_subsonic_phase2 import _playlist


@pytest.fixture
def lib(base_config):
    """Admin + one player user ("jogger"); three same-artist tracks close in
    tempo, all inside the player's one playlist, so both accounts may reach
    every track — dislikes are the only thing that should ever differ."""
    app = _app(base_config, run_presets=[{"name": "Tempo", "bpm": 120}])
    admin = app.test_client()
    csrf = _login(admin)
    db_path, music = base_config["db_path"], base_config["music_dir"]
    paths = {
        "a": os.path.join(music, "Artist", "Album", "01.flac"),
        "b": os.path.join(music, "Artist", "Album", "02.flac"),
        "c": os.path.join(music, "Artist", "Album", "03.flac"),
    }
    for p in paths.values():
        _flac(p)
    ids = {
        "a": _insert(db_path, paths["a"], title="A", artist="Artist", album="Album",
                     album_artist="Artist", track_no=1, bpm=120.0, duration_ms=180000),
        "b": _insert(db_path, paths["b"], title="B", artist="Artist", album="Album",
                     album_artist="Artist", track_no=2, bpm=121.0, duration_ms=180000),
        "c": _insert(db_path, paths["c"], title="C", artist="Artist", album="Album",
                     album_artist="Artist", track_no=3, bpm=122.0, duration_ms=180000),
    }
    local = _playlist(db_path, "Mine", list(paths.values()))
    st = app.extensions["state"]
    player_id = st.db.add_player("jogger", "x", playlist_ids=[local])
    creds = {}
    for owner in ("admin", f"player:{player_id}"):
        creds[owner] = admin.post(f"/api/subsonic/accounts/{owner}/api-key",
                                  headers=csrf).get_json()["api_key"]
    client = app.test_client()

    def call(method, as_="admin", **params):
        key = creds["admin" if as_ == "admin" else f"player:{player_id}"]
        return client.get(f"/rest/{method}", query_string={"apiKey": key, "f": "json", **params})

    return {"app": app, "st": st, "admin": admin, "csrf": csrf, "call": call, "paths": paths,
            "ids": ids, "local": local, "player_id": player_id}


def _body(r):
    return _json(r)


def _owner(lib_, as_):
    return "admin" if as_ == "admin" else f"player:{lib_['player_id']}"


# ── setRating ─────────────────────────────────────────────────────────────────

def test_set_rating_writes_and_clears(lib):
    sid = f"tr-{lib['ids']['a']}"
    assert _body(lib["call"]("setRating", id=sid, rating=4))["status"] == "ok"
    assert lib["st"].db.get_mark("admin", lib["paths"]["a"])["rating"] == 4
    assert _body(lib["call"]("setRating", id=sid, rating=0))["status"] == "ok"
    assert lib["st"].db.get_mark("admin", lib["paths"]["a"])["rating"] is None


def test_set_rating_validates_range(lib):
    sid = f"tr-{lib['ids']['a']}"
    for bad in (6, -1, "nope"):
        err = _body(lib["call"]("setRating", id=sid, rating=bad))["error"]
        assert err["code"] == 0, bad
    err = _body(lib["call"]("setRating", id=sid))["error"]  # rating omitted entirely
    assert err["code"] == 10


def test_set_rating_only_supports_songs(lib):
    song = _body(lib["call"]("getSong", id=f"tr-{lib['ids']['a']}"))["song"]
    for aid in (song["albumId"], song["artistId"]):
        err = _body(lib["call"]("setRating", id=aid, rating=3))["error"]
        assert err["code"] == 0


def test_set_rating_out_of_scope_is_70(lib):
    # "c" is inside the player's own playlist, so use a track outside it instead:
    # every track here is shared, so scope it down to an admin-only track.
    outside = os.path.join(os.path.dirname(lib["paths"]["a"]), "outside.flac")
    _flac(outside)
    tid = _insert(lib["app"].extensions["state"].config["db_path"], outside, title="Outside",
                 artist="Stranger", album="Elsewhere")
    err = _body(lib["call"]("setRating", as_="player", id=f"tr-{tid}", rating=3))["error"]
    assert err["code"] == 70


def test_set_rating_registered_in_methods(lib):
    from bpm_tagger.web.subsonic.handlers import METHODS
    assert "setRating" in METHODS


# ── userRating / per-owner star isolation ──────────────────────────────────────

def test_user_rating_is_per_owner_and_invisible_across_accounts(lib):
    sid = f"tr-{lib['ids']['a']}"
    lib["call"]("setRating", id=sid, rating=5)
    admin_song = _body(lib["call"]("getSong", id=sid))["song"]
    player_song = _body(lib["call"]("getSong", as_="player", id=sid))["song"]
    assert admin_song.get("userRating") == 5
    assert "userRating" not in player_song  # unrated for the player -> omitted
    assert admin_song["starred"]
    assert not player_song.get("starred")

    lib["call"]("setRating", as_="player", id=sid, rating=2)
    player_song = _body(lib["call"]("getSong", as_="player", id=sid))["song"]
    admin_song = _body(lib["call"]("getSong", id=sid))["song"]
    assert player_song.get("userRating") == 2
    assert admin_song.get("userRating") == 5  # untouched by the player's rating


def test_star_sets_rating_four_and_unstar_of_five_drops_to_three(lib):
    sid = f"tr-{lib['ids']['a']}"
    path = lib["paths"]["a"]
    lib["call"]("star", id=sid)
    mark = lib["st"].db.get_mark("admin", path)
    assert mark == {"rating": 4, "disliked": False, "starred": True}

    lib["call"]("setRating", id=sid, rating=5)
    lib["call"]("unstar", id=sid)
    mark = lib["st"].db.get_mark("admin", path)
    assert mark == {"rating": 3, "disliked": False, "starred": False}


def test_star_unstar_is_per_owner(lib):
    sid = f"tr-{lib['ids']['b']}"
    lib["call"]("star", as_="player", id=sid)
    assert lib["st"].db.get_mark(_owner(lib, "player"), lib["paths"]["b"])["starred"] is True
    assert lib["st"].db.get_mark("admin", lib["paths"]["b"])["starred"] is False
    # The admin's library-wide projection never moves for a player's star (D4).
    assert lib["st"].db.get_track(lib["paths"]["b"])["starred"] == 0


def test_get_starred2_is_per_owner(lib):
    lib["call"]("setRating", id=f"tr-{lib['ids']['a']}", rating=4)
    lib["call"]("setRating", as_="player", id=f"tr-{lib['ids']['b']}", rating=5)
    admin_titles = {s["title"] for s in _body(lib["call"]("getStarred2"))["starred2"]["song"]}
    player_titles = {s["title"]
                     for s in _body(lib["call"]("getStarred2", as_="player"))["starred2"]["song"]}
    assert admin_titles == {"A"}
    assert player_titles == {"B"}


# ── Rating-weighted picking skips only the caller's own dislikes ───────────────

def test_random_songs_excludes_only_the_callers_dislike(lib):
    lib["st"].db.set_owner_disliked("admin", lib["paths"]["a"], True)
    admin_titles = {s["title"] for s in
                    _body(lib["call"]("getRandomSongs", size=10))["randomSongs"]["song"]}
    assert admin_titles == {"B", "C"}
    player_titles = {s["title"] for s in
                     _body(lib["call"]("getRandomSongs", as_="player", size=10)
                          )["randomSongs"]["song"]}
    assert player_titles == {"A", "B", "C"}  # the admin's dislike doesn't reach the player


def test_similar_songs_excludes_only_the_callers_dislike(lib):
    lib["st"].db.set_owner_disliked("admin", lib["paths"]["b"], True)
    seed = f"tr-{lib['ids']['a']}"
    admin_titles = {s["title"] for s in
                    _body(lib["call"]("getSimilarSongs", id=seed, count=10))["similarSongs"]["song"]}
    assert "B" not in admin_titles and "C" in admin_titles
    player_titles = {s["title"] for s in
                     _body(lib["call"]("getSimilarSongs2", as_="player", id=seed, count=10)
                          )["similarSongs2"]["song"]}
    assert "B" in player_titles


def test_run_playlist_excludes_only_the_callers_dislike(lib):
    lib["st"].db.set_owner_disliked("admin", lib["paths"]["c"], True)
    admin_entries = _body(lib["call"]("getPlaylist", id="pl-run-0"))["playlist"]["entry"]
    assert {e["title"] for e in admin_entries} == {"A", "B"}
    player_entries = _body(lib["call"]("getPlaylist", as_="player", id="pl-run-0")
                          )["playlist"]["entry"]
    assert {e["title"] for e in player_entries} == {"A", "B", "C"}
