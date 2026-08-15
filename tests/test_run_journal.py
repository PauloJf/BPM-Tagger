"""Per-account play attribution, the run journal, and per-user run stats.

Covers the owner-key convention (admin / guest / named player), the
server-derived run lifecycle (grouping, source rollover, idle close, explicit
end), the journal payload math, owner filtering, and — the regression that
matters most — that the pre-existing cumulative run_stats totals keep updating
exactly as they did before attribution existed.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from bpm_tagger.db import BPMDatabase
from bpm_tagger.db.runs import RUN_IDLE_SECONDS


# ── helpers ─────────────────────────────────────────────────────────────────

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


def _login(client, **body):
    r = client.post("/api/login", json=body)
    assert r.status_code == 200, r.get_json()
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _stat(client, csrf, deltas, run=None, end=False):
    body = {"deltas": deltas}
    if run is not None:
        body["run"] = run
    if end:
        body["end"] = True
    r = client.post("/api/run/stat", json=body, headers=csrf)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


LIB = {"source": "library", "target": 160}

# One flush's worth of a tempo-locked run: 60s on feet, all of it stretched, at
# a 160 BPM cadence (cadence_weighted = target × wall_ms).
BATCH = {"wall_ms": 60_000, "shifted_ms": 60_000, "native_ms": 55_000,
         "cadence_weighted": 160 * 60_000, "tracks_played": 2, "cad_160": 60_000}


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _age_run(db, run_id, seconds):
    """Backdate a run's timestamps so idle/lifecycle rules can be exercised
    without sleeping."""
    with db._connect() as conn:
        row = conn.execute("SELECT started_at, last_event_at FROM runs WHERE id = ?",
                           (run_id,)).fetchone()
        shift = timedelta(seconds=seconds)
        conn.execute(
            "UPDATE runs SET started_at = ?, last_event_at = ? WHERE id = ?",
            ((datetime.fromisoformat(row["started_at"]) - shift).isoformat(),
             (datetime.fromisoformat(row["last_event_at"]) - shift).isoformat(),
             run_id))


# ── owner-key resolution ────────────────────────────────────────────────────

def test_owner_key_admin_guest_and_named_player(base_config):
    app = _app(base_config, run_password="runner99")
    db = app.extensions["state"].db

    admin = app.test_client()
    acsrf = _login(admin, password="s3cret")
    db.add_player("runner", generate_password_hash("runrunrun"))
    pid = db.get_player_by_username("runner")["id"]

    guest = app.test_client()
    gcsrf = _login(guest, password="runner99")
    player = app.test_client()
    pcsrf = _login(player, username="runner", password="runrunrun")

    _stat(admin, acsrf, BATCH, LIB)
    _stat(guest, gcsrf, BATCH, LIB)
    _stat(player, pcsrf, BATCH, LIB)

    assert db.list_run_owners() == sorted(["admin", "guest", f"player:{pid}"])
    assert {r["owner"] for r in db.list_runs(50)} == {"admin", "guest", f"player:{pid}"}
    # Every account's own cumulative slice holds exactly one batch...
    for owner in ("admin", "guest", f"player:{pid}"):
        assert db.get_run_stats_for_owner(owner)["wall_ms"] == 60_000
    # ...and the account-blind total holds all three.
    assert db.get_run_stats()["wall_ms"] == 180_000


def test_every_guest_device_shares_one_bucket(base_config):
    app = _app(base_config, run_password="runner99")
    db = app.extensions["state"].db
    for _ in range(2):
        c = app.test_client()
        _stat(c, _login(c, password="runner99"), BATCH, LIB)
    assert db.list_run_owners() == ["guest"]
    # Both devices land in the same open run (same owner, same source).
    assert len(db.list_runs(50)) == 1


# ── run lifecycle ───────────────────────────────────────────────────────────

def test_consecutive_events_group_into_one_run(base_config):
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    first = _stat(client, csrf, BATCH, LIB)["run_id"]
    second = _stat(client, csrf, BATCH, LIB)["run_id"]
    assert first == second

    runs = db.list_runs(50)
    assert len(runs) == 1
    assert runs[0]["played_ms"] == 120_000
    assert runs[0]["tracks_played"] == 4
    assert runs[0]["ended_at"] is None          # still open


def test_changing_source_rolls_over_to_a_new_run(base_config):
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    a = _stat(client, csrf, BATCH, LIB)["run_id"]
    b = _stat(client, csrf, BATCH, {"source": "playlist:7", "target": 170})["run_id"]
    assert a != b
    runs = {r["id"]: r for r in db.list_runs(50)}
    assert runs[a]["ended_at"] is not None      # the library run was closed
    assert runs[a]["source"] == "library"
    assert runs[b]["source"] == "playlist:7" and runs[b]["ended_at"] is None
    assert runs[b]["target_bpm"] == 170


def test_idle_gap_closes_the_run_and_opens_a_new_one(base_config):
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    first = _stat(client, csrf, BATCH, LIB)["run_id"]
    _age_run(db, first, RUN_IDLE_SECONDS + 60)   # the phone died mid-run
    second = _stat(client, csrf, BATCH, LIB)["run_id"]

    assert second != first
    closed = [r for r in db.list_runs(50) if r["id"] == first][0]
    # Closed at its LAST EVENT, not at the moment the next run started.
    assert closed["ended_at"] == closed["last_event_at"]


def test_end_flag_closes_the_run(base_config):
    """Playing something else (queue replaced) / releasing the tempo lock: the
    client flushes with end=true and the run is closed at its last event."""
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    run_id = _stat(client, csrf, BATCH, LIB)["run_id"]
    assert _stat(client, csrf, {}, end=True)["run_id"] is None
    row = [r for r in db.list_runs(50) if r["id"] == run_id][0]
    assert row["ended_at"] == row["last_event_at"]
    assert db.get_open_run("admin") is None

    # A later run starts fresh rather than reviving the closed one.
    assert _stat(client, csrf, BATCH, LIB)["run_id"] != run_id


def test_end_is_idempotent_and_scoped_to_the_owner(base_config):
    app = _app(base_config, run_password="runner99")
    db = app.extensions["state"].db
    admin, guest = app.test_client(), app.test_client()
    acsrf = _login(admin, password="s3cret")
    gcsrf = _login(guest, password="runner99")

    _stat(admin, acsrf, BATCH, LIB)
    _stat(guest, gcsrf, BATCH, LIB)
    _stat(admin, acsrf, {}, end=True)
    _stat(admin, acsrf, {}, end=True)            # idempotent

    assert db.get_open_run("admin") is None
    assert db.get_open_run("guest") is not None   # the guest's run is untouched


def test_events_without_run_context_never_open_a_run(base_config):
    """A batch with no run context still counts toward the totals — it just has
    no run to belong to (the client only sends context under a tempo lock)."""
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    assert _stat(client, csrf, BATCH)["run_id"] is None
    assert db.list_runs(50) == []
    assert db.get_run_stats()["wall_ms"] == 60_000
    assert db.get_run_stats_for_owner("admin")["wall_ms"] == 60_000


# ── cumulative aggregates must be untouched ─────────────────────────────────

def test_cumulative_totals_update_exactly_as_before(base_config):
    """The account-blind run_stats table keeps its old behaviour: same keys,
    same sums, one row per key, no double counting from the new mirrors."""
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    before = db.get_run_stats()
    assert before == {}
    _stat(client, csrf, BATCH, LIB)
    _stat(client, csrf, BATCH, LIB)

    totals = db.get_run_stats()
    assert totals == {"wall_ms": 120_000, "shifted_ms": 120_000, "native_ms": 110_000,
                      "cadence_weighted": 2 * 160 * 60_000, "tracks_played": 4,
                      "cad_160": 120_000}
    # /api/stats' "run" block is the same untouched dict.
    assert client.get("/api/stats").get_json()["run"] == totals
    # The per-account mirror sums to exactly the same numbers (no pre-existing
    # history in a fresh DB) — so "All" and the owner slices agree.
    assert db.get_run_stats_attributed() == totals


def test_db_level_add_run_stats_without_owner_is_unchanged(db):
    """The old single-argument call still writes only the global counters."""
    db.add_run_stats({"wall_ms": 1000, "bogus key!": 5, "neg": -1})
    assert db.get_run_stats() == {"wall_ms": 1000}
    assert db.get_run_stats_attributed() == {}
    assert db.list_runs(10) == []


# ── journal payload math + owner filtering ──────────────────────────────────

def test_journal_payload_math(base_config):
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    # 60s at 160 BPM fully stretched + 60s at 150 BPM native.
    _stat(client, csrf, BATCH, LIB)
    _stat(client, csrf, {"wall_ms": 60_000, "shifted_ms": 0, "native_ms": 60_000,
                         "cadence_weighted": 150 * 60_000, "tracks_played": 1},
          {"source": "library", "target": 150})

    item = client.get("/api/stats/runs").get_json()["items"][0]
    assert item["owner"] == "admin" and item["owner_label"] == "Admin"
    assert item["source"] == "library" and item["source_label"] == "Library"
    assert item["tracks"] == 3
    assert item["played_ms"] == 120_000
    assert item["stretched_pct"] == 50                    # 60s of 120s
    assert round(item["avg_cadence"]) == 155              # time-weighted (160,150)
    assert item["target_bpm"] == 150                      # LAST reported target
    assert item["open"] is True
    # Duration spans the first event's coverage → at least the time on feet.
    assert item["duration_ms"] >= 120_000

    run_id = item["id"]
    _age_run(db, run_id, RUN_IDLE_SECONDS + 60)
    stale = client.get("/api/stats/runs").get_json()["items"][0]
    assert stale["open"] is False                          # past the idle window
    assert stale["ended_at"] == stale["started_at"] or stale["duration_ms"] >= 120_000


def test_journal_names_the_playlist_source(base_config):
    app = _app(base_config)
    db = app.extensions["state"].db
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("INSERT INTO playlists (source, name) VALUES ('local', 'Tempo 160')")
    conn.commit()
    pid = db.list_playlists()[0]["id"]
    conn.close()

    client = app.test_client()
    csrf = _login(client, password="s3cret")
    _stat(client, csrf, BATCH, {"source": f"playlist:{pid}", "target": 160})

    item = client.get("/api/stats/runs").get_json()["items"][0]
    assert item["source"] == f"playlist:{pid}"
    assert item["source_label"] == "Tempo 160"


def test_journal_pages_like_most_played(base_config):
    app = _app(base_config)
    db = app.extensions["state"].db
    client = app.test_client()
    _login(client, password="s3cret")
    now = datetime.now(timezone.utc)
    with db._connect() as conn:
        for i in range(17):
            stamp = (now - timedelta(hours=i)).isoformat()
            conn.execute(
                "INSERT INTO runs (owner, started_at, last_event_at, ended_at, source, "
                "played_ms) VALUES ('admin', ?, ?, ?, 'library', 1000)",
                (stamp, stamp, stamp))

    page1 = client.get("/api/stats/runs").get_json()
    assert len(page1["items"]) == 15 and page1["has_more"] is True
    page2 = client.get("/api/stats/runs?offset=15").get_json()
    assert len(page2["items"]) == 2 and page2["has_more"] is False
    assert {r["id"] for r in page1["items"]}.isdisjoint({r["id"] for r in page2["items"]})
    # Newest first, no overlap.
    assert page1["items"][0]["started_at"] > page1["items"][1]["started_at"]


def test_owner_filtering_of_journal_and_cumulative_stats(base_config):
    app = _app(base_config, run_password="runner99")
    admin, guest = app.test_client(), app.test_client()
    acsrf = _login(admin, password="s3cret")
    gcsrf = _login(guest, password="runner99")

    _stat(admin, acsrf, BATCH, LIB)
    _stat(guest, gcsrf, BATCH, LIB)
    _stat(guest, gcsrf, BATCH, LIB)

    mine = admin.get("/api/stats/runs?owner=admin").get_json()["items"]
    assert [r["owner"] for r in mine] == ["admin"]
    theirs = admin.get("/api/stats/runs?owner=guest").get_json()["items"]
    assert [r["owner_label"] for r in theirs] == ["Guest"]
    assert theirs[0]["played_ms"] == 120_000

    everything = admin.get("/api/stats/runs").get_json()["items"]
    assert len(everything) == 2

    # Cumulative slices: All is the untouched total, each owner its own.
    all_run = admin.get("/api/stats/run").get_json()
    assert all_run["run"]["wall_ms"] == 180_000 and all_run["attributed"] is False
    assert admin.get("/api/stats/run?owner=admin").get_json()["run"]["wall_ms"] == 60_000
    assert admin.get("/api/stats/run?owner=guest").get_json()["run"]["wall_ms"] == 120_000
    # Nothing pre-dates attribution in a fresh DB → no unattributed remainder.
    assert admin.get("/api/stats/run?owner=unattributed").get_json()["run"] == {}
    assert [o["key"] for o in admin.get("/api/stats").get_json()["run_owners"]] == \
        ["admin", "guest"]


def test_unattributed_bucket_represents_pre_upgrade_history(base_config):
    """History recorded before attribution has no owner. It stays visible under
    All and is offered as its own honest bucket — never silently reassigned."""
    app = _app(base_config)
    db = app.extensions["state"].db
    db.add_run_stats({"wall_ms": 500_000, "tracks_played": 9})   # legacy rows

    client = app.test_client()
    csrf = _login(client, password="s3cret")
    _stat(client, csrf, BATCH, LIB)

    payload = client.get("/api/stats").get_json()
    assert payload["run"]["wall_ms"] == 560_000                   # unchanged meaning
    assert [o["key"] for o in payload["run_owners"]] == ["admin", "unattributed"]

    rest = client.get("/api/stats/run?owner=unattributed").get_json()["run"]
    assert rest["wall_ms"] == 500_000 and rest["tracks_played"] == 9
    # The journal has nothing to show for it — those runs were never recorded.
    assert client.get("/api/stats/runs?owner=unattributed").get_json() == {
        "items": [], "has_more": False}


def test_run_endpoints_reject_a_bogus_owner(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client, password="s3cret")
    for bad in ("player:x", "root", "player:1;drop", "admin'"):
        assert client.get(f"/api/stats/run?owner={bad}").status_code == 400, bad
        assert client.get(f"/api/stats/runs?owner={bad}").status_code == 400, bad
    assert client.get("/api/stats/runs?offset=nope").status_code == 400


def test_journal_endpoints_are_admin_only(base_config):
    """Nothing was added to the player allowlist — a kiosk session is 403'd."""
    app = _app(base_config, run_password="runner99")
    player = app.test_client()
    _login(player, password="runner99")
    assert player.get("/api/stats/runs").status_code == 403
    assert player.get("/api/stats/run").status_code == 403


# ── per-account play events (scrobble path) ─────────────────────────────────

def _seed_track(base_config, name="song"):
    path = f"{base_config['music_dir']}/{name}.mp3"
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("INSERT INTO tracks (file_path, title, bpm, status) "
                 "VALUES (?, ?, 120.0, 'done')", (path, name))
    conn.commit()
    conn.close()
    return path


def test_scrobble_attributes_the_play_to_the_account_and_run(base_config):
    app = _app(base_config, run_password="runner99")
    db = app.extensions["state"].db
    path = _seed_track(base_config)

    admin = app.test_client()
    acsrf = _login(admin, password="s3cret")
    run_id = _stat(admin, acsrf, BATCH, LIB)["run_id"]
    r = admin.post("/api/scrobble", json={"path": path, "duration_ms": 210_000,
                                          "run": {"target": 160, "stretched": True}},
                   headers=acsrf)
    assert r.status_code == 200 and r.get_json()["ok"] is True

    guest = app.test_client()
    gcsrf = _login(guest, password="runner99")
    assert guest.post("/api/scrobble", json={"path": path}, headers=gcsrf).status_code == 200

    events = db.list_play_events(50)
    assert len(events) == 2
    by_owner = {e["owner"]: e for e in events}
    assert by_owner["admin"]["run_id"] == run_id
    assert by_owner["admin"]["cadence"] == 160 and by_owner["admin"]["stretched"] == 1
    assert by_owner["admin"]["duration_ms"] == 210_000
    # A plain (non-run) play is attributed to the account but to no run.
    assert by_owner["guest"]["run_id"] is None
    assert by_owner["guest"]["cadence"] is None

    # The library-global play count is untouched by attribution: +1 per scrobble.
    assert db.get_track(path)["play_count"] == 2
    assert db.list_play_events(50, owner="guest") == [by_owner["guest"]]


def test_a_scrobble_can_be_the_first_event_of_a_run(base_config):
    """A short track passes halfway well inside the client's ~20s flush window,
    so the scrobble can arrive before any run-stat batch. It opens the run —
    otherwise that play would be detached from its run forever — and the flush
    that follows CONTINUES it rather than forking a second one."""
    app = _app(base_config)
    db = app.extensions["state"].db
    path = _seed_track(base_config)
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    r = client.post("/api/scrobble",
                    json={"path": path, "duration_ms": 100_000,
                          "run": {"source": "library", "target": 160, "stretched": True}},
                    headers=csrf)
    assert r.status_code == 200
    runs = db.list_runs(50)
    assert len(runs) == 1 and runs[0]["source"] == "library"
    assert db.list_play_events(10)[0]["run_id"] == runs[0]["id"]

    assert _stat(client, csrf, BATCH, LIB)["run_id"] == runs[0]["id"]
    after = db.list_runs(50)
    assert len(after) == 1                        # continued, not duplicated
    assert after[0]["played_ms"] == 60_000        # only the flush reports time


def test_play_event_does_not_join_a_closed_or_stale_run(base_config):
    """The lifecycle is the same one the stat path uses: a stale or closed run is
    never revived — the play opens a fresh run instead."""
    app = _app(base_config)
    db = app.extensions["state"].db
    path = _seed_track(base_config)
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    run_id = _stat(client, csrf, BATCH, LIB)["run_id"]
    _age_run(db, run_id, RUN_IDLE_SECONDS + 60)
    client.post("/api/scrobble", json={"path": path, "run": {"target": 160}}, headers=csrf)
    revived = db.list_play_events(10)[0]["run_id"]
    assert revived is not None and revived != run_id
    stale = [r for r in db.list_runs(50) if r["id"] == run_id][0]
    assert stale["ended_at"] == stale["last_event_at"]   # closed at its last event

    _stat(client, csrf, {}, end=True)
    client.post("/api/scrobble", json={"path": path, "run": {"target": 160}}, headers=csrf)
    assert db.list_play_events(10)[0]["run_id"] not in (None, run_id, revived)


def test_scrobble_survives_a_broken_attribution(base_config, monkeypatch):
    """Attribution rides the playback path — it must never fail a play report."""
    app = _app(base_config)
    db = app.extensions["state"].db
    path = _seed_track(base_config)
    client = app.test_client()
    csrf = _login(client, password="s3cret")

    def boom(*a, **kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(type(db), "record_play_event", boom)
    r = client.post("/api/scrobble", json={"path": path}, headers=csrf)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert db.get_track(path)["play_count"] == 1
