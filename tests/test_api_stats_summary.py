"""GET /api/stats/summary: its own token, 404 when disabled, the JSON shape, listening windows (Europe/Lisbon)."""

from datetime import datetime, timezone

import pytest

from bpm_tagger.db.stats_summary import window_starts

TOKEN = "stats-token-0123456789"
H = {"Authorization": f"Bearer {TOKEN}"}
URL = "/api/stats/summary"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


@pytest.fixture
def base_config(base_config):
    base_config["stats_api_token"] = TOKEN
    return base_config


@pytest.fixture(autouse=True)
def _fresh_throttle():
    from bpm_tagger.web.api import stats_summary as mod
    mod._throttle = mod._Throttle()


def _db(client):
    return client.application.extensions["state"].db


def track(db, fp, title, artist, album="Alb", duration_ms=200000, size=1000, status="done"):
    stamp = f"{size}:1700000000.0"
    db.upsert_track(fp, stamp, 120.0, None, None, 120.0, 0.9, "librosa", status)
    db.update_track_tags(fp, {
        "title": title, "artist": artist, "album": album, "album_artist": artist, "track_no": 1, "disc_no": 1,
        "year": 2020, "isrc": "", "duration_ms": duration_ms,
        "norm_title": title.lower(), "norm_artist": artist.lower(),
    }, stamp)


def play(db, owner, fp, at, duration_ms=None):
    with db._connect() as conn:
        conn.execute("INSERT INTO play_events (owner, file_path, played_at, duration_ms) VALUES (?,?,?,?)",
                     (owner, fp, at, duration_ms))
        conn.commit()


# ── auth ──────────────────────────────────────────────────────────────────────
def test_disabled_when_the_token_is_unset(base_config):
    from conftest import _make_app
    base_config["stats_api_token"] = ""
    c = _make_app(base_config).test_client()
    assert c.get(URL).status_code == 404
    assert c.get(URL, headers=H).status_code == 404
    assert c.get(URL, headers={"Authorization": "Bearer "}).status_code == 404


def test_missing_and_wrong_token_are_401(client):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(URL, headers={"Authorization": TOKEN}).status_code == 401
    assert client.get(URL, headers={"Authorization": f"Bearer {TOKEN}x"}).status_code == 401


def test_a_login_session_alone_does_not_open_it(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.get(URL).status_code == 401


def test_the_token_opens_nothing_else_and_needs_no_session(client):
    assert client.get("/api/stats", headers=H).status_code in (401, 403)
    r = client.get(URL, headers=H)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in r.headers          # no session or CSRF cookie for a dashboard poll


def test_a_player_session_cookie_does_not_open_it(client):
    with client.session_transaction() as s:
        s["role"] = "player"
    assert client.get(URL).status_code in (401, 403)


def test_wrong_tokens_trip_the_lockout(client):
    for _ in range(10):
        assert client.get(URL, headers={"Authorization": "Bearer bad"}).status_code == 401
    r = client.get(URL, headers=H)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_the_token_is_never_listed_in_settings(client):
    client.post("/api/login", json={"password": "s3cret"})
    assert TOKEN not in client.get("/api/settings").get_data(as_text=True)


# ── shape ─────────────────────────────────────────────────────────────────────
def test_shape_on_an_empty_library(client):
    d = client.get(URL, headers=H).get_json()
    keys = ["library_tracks", "library_artists", "library_albums", "library_size_bytes", "library_minutes",
            "tracks_analysed", "tracks_pending", "tracks_errors", "tracks_needs_review",
            "top_artist_month", "top_artist_month_plays", "top_track_month", "top_track_month_artist",
            "top_track_month_plays", "generated_at", "timezone"]
    keys += [f"{m}_{w}" for m in ("plays", "minutes", "artists") for w in ("week", "month", "year", "all")]
    for k in keys:
        assert k in d, k
    assert d["library_tracks"] == 0 and d["plays_all"] == 0 and d["minutes_all"] == 0.0
    assert d["top_artist_month"] == "" and d["top_track_month"] == "" and d["timezone"] == "Europe/Lisbon"
    assert "owners" not in d and "owner" not in d


def test_values_are_flat_and_typed(client):
    db = _db(client)
    track(db, "/m/a.mp3", "A", "X")
    play(db, "admin", "/m/a.mp3", datetime.now(timezone.utc).isoformat())
    d = client.get(URL + "?by_owner=1", headers=H).get_json()
    for k, v in d.items():
        if k == "owners":
            assert all(isinstance(x, (int, float, str)) for row in v.values() for x in row.values())
        else:
            assert isinstance(v, (int, float, str)), k
    assert isinstance(d["minutes_week"], float) and isinstance(d["library_size_bytes"], int)


def test_library_and_tagging_totals(client):
    db = _db(client)
    track(db, "/m/a.mp3", "A", "Alpha", album="One", duration_ms=180000, size=4000)
    track(db, "/m/b.mp3", "B", "Alpha", album="One", duration_ms=120000, size=6000)
    track(db, "/m/c.mp3", "C", "Beta", album="Two", duration_ms=60000, size=500)
    track(db, "/m/p.mp3", "P", "Beta", album="Two", duration_ms=60000, size=500, status="pending")
    track(db, "/m/d.mp3", "D", "Gone", album="Three", duration_ms=60000, size=999, status="deleted")
    with db._connect() as conn:
        conn.execute("UPDATE tracks SET needs_review=1 WHERE file_path='/m/a.mp3'")
        conn.commit()
    d = client.get(URL, headers=H).get_json()
    assert d["library_tracks"] == 4 and d["library_artists"] == 2 and d["library_albums"] == 2
    assert d["library_size_bytes"] == 4000 + 6000 + 500 + 500
    assert d["library_minutes"] == 7.0
    assert (d["tracks_analysed"], d["tracks_pending"], d["tracks_needs_review"]) == (3, 1, 1)


# ── listening ─────────────────────────────────────────────────────────────────
def test_listening_totals_artists_and_fallback_length(client):
    db = _db(client)
    track(db, "/m/a.mp3", "A", "Alpha", duration_ms=240000)
    track(db, "/m/b.mp3", "B", "Beta", duration_ms=180000)
    now = datetime.now(timezone.utc).isoformat()
    play(db, "admin", "/m/a.mp3", now, duration_ms=60000)      # the length the player reported wins: 1 min
    play(db, "admin", "/m/a.mp3", now)                         # falls back to the track's own: 4 min
    play(db, "guest", "/m/b.mp3", now)                         # 3 min, another account
    play(db, "admin", "/m/gone.mp3", now, duration_ms=60000)   # not in the library: counts, but has no artist
    d = client.get(URL, headers=H).get_json()
    assert d["plays_all"] == 4 and d["minutes_all"] == 9.0 and d["artists_all"] == 2
    assert d["plays_week"] == 4
    one = client.get(URL + "?owner=guest", headers=H).get_json()
    assert one["owner"] == "guest" and one["plays_all"] == 1 and one["minutes_all"] == 3.0 and one["artists_all"] == 1
    assert client.get(URL + "?owner=x%27%3B--", headers=H).status_code == 400
    assert client.get(URL + "?owner=player:9", headers=H).get_json()["plays_all"] == 0


def test_by_owner_breakdown_names_players(client):
    db = _db(client)
    track(db, "/m/a.mp3", "A", "Alpha", duration_ms=120000)
    pid = db.add_player("kid", "hash")
    pid = pid["id"] if isinstance(pid, dict) else pid
    now = datetime.now(timezone.utc).isoformat()
    play(db, "admin", "/m/a.mp3", now)
    play(db, f"player:{pid}", "/m/a.mp3", now)
    d = client.get(URL + "?by_owner=1", headers=H).get_json()
    assert d["owners"]["admin"]["plays_all"] == 1 and d["owners"]["admin"]["minutes_all"] == 2.0
    assert d["owners"]["admin"]["name"] == "admin"
    assert d["owners"][f"player:{pid}"]["name"] == "kid"


def test_top_artist_and_track_of_the_month(client):
    db = _db(client)
    track(db, "/m/a.mp3", "Alpha One", "Alpha", duration_ms=100000)
    track(db, "/m/b.mp3", "Alpha Two", "Alpha", duration_ms=100000)
    track(db, "/m/c.mp3", "Beta One", "Beta", duration_ms=50000)
    stamp = datetime.now(timezone.utc).isoformat()
    for fp in ("/m/a.mp3", "/m/b.mp3", "/m/c.mp3"):
        play(db, "admin", fp, stamp)
    play(db, "admin", "/m/c.mp3", stamp)
    for day in ("01", "02", "03"):                                 # old plays do not count towards the month
        play(db, "admin", "/m/c.mp3", f"2001-01-{day}T00:00:00+00:00")
    d = client.get(URL, headers=H).get_json()
    assert (d["top_artist_month"], d["top_artist_month_plays"]) == ("Alpha", 2)   # ties Beta on plays at 2; more minutes listened wins
    assert (d["top_track_month"], d["top_track_month_artist"], d["top_track_month_plays"]) == ("Beta One", "Beta", 2)
    assert d["plays_all"] == 7


def test_listening_window_boundaries(client, monkeypatch):
    fixed = utc(2026, 7, 15, 10)                                       # Wednesday, Lisbon on UTC+1
    from bpm_tagger.web.api import stats_summary as api
    monkeypatch.setattr(api, "window_starts", lambda now=None: window_starts(fixed))
    db = _db(client)
    track(db, "/m/a.mp3", "A", "Alpha", duration_ms=60000)
    for at in ("2026-07-12T22:59:59+00:00",    # Sunday 23:59:59 Lisbon: last week
               "2026-07-12T23:00:00+00:00",    # Monday 00:00:00 Lisbon: this week
               "2026-06-30T23:00:00+00:00",    # 1 July 00:00 Lisbon: this month
               "2026-06-30T22:59:59+00:00",    # June
               "2026-01-01T00:00:00+00:00",    # this year, first instant
               "2025-12-31T23:59:59+00:00"):   # last year
        play(db, "admin", "/m/a.mp3", at)
    d = client.get(URL, headers=H).get_json()
    assert (d["plays_week"], d["plays_month"], d["plays_year"], d["plays_all"]) == (1, 3, 5, 6)
    assert d["minutes_week"] == 1.0 and d["artists_week"] == 1


# ── calendar windows ──────────────────────────────────────────────────────────
def test_window_starts_in_winter_time():
    s = window_starts(utc(2026, 11, 1, 12))                            # a Sunday, Lisbon is on UTC+0
    assert s == {"week": utc(2026, 10, 26), "month": utc(2026, 11, 1), "year": utc(2026, 1, 1)}


def test_window_starts_in_summer_time_and_across_midnight():
    s = window_starts(utc(2026, 7, 15, 10))
    assert s["week"] == utc(2026, 7, 12, 23) and s["month"] == utc(2026, 6, 30, 23) and s["year"] == utc(2026, 1, 1)
    assert window_starts(utc(2026, 6, 30, 23, 30))["month"] == utc(2026, 6, 30, 23)      # already 1 July in Lisbon
    assert window_starts(utc(2026, 6, 30, 22, 30))["month"] == utc(2026, 5, 31, 23)
    assert window_starts(utc(2026, 7, 12, 23, 30))["week"] == utc(2026, 7, 12, 23)       # Monday 00:30 locally
    assert window_starts(utc(2026, 7, 12, 22, 30))["week"] == utc(2026, 7, 5, 23)
