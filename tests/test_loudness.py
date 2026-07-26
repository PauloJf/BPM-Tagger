"""Loudness measurement, ReplayGain tag reading, storage, and the back-fill API.

Test audio is synthesised into real WAV/FLAC files rather than shipped as
fixtures, so the numbers are checked against something we know the true level of:
halving amplitude must read exactly 6 dB quieter, which is the property the whole
feature rests on.
"""

import numpy as np
import pytest
import soundfile as sf

from bpm_tagger.bpm.loudness import (
    REPLAYGAIN_REF_LUFS,
    _parse_gain_db,
    analyze_loudness,
    gain_db_for,
    measure_loudness,
    read_loudness_tag,
)
from bpm_tagger.db import BPMDatabase

pyloudnorm = pytest.importorskip("pyloudnorm")

SR = 44100


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _tone(path, seconds=4.0, amplitude=0.5, ext="wav"):
    """Write a stereo 1 kHz tone. Deterministic, and its level is exactly known."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    mono = amplitude * np.sin(2 * np.pi * 1000 * t)
    sf.write(str(path), np.column_stack([mono, mono]), SR, format=ext.upper())
    return str(path)


# ── measurement ──────────────────────────────────────────────────────────────

def test_measures_a_plausible_level(tmp_path):
    lufs = measure_loudness(_tone(tmp_path / "tone.wav"))
    assert lufs is not None
    # A -6 dBFS tone lands well inside the range real music occupies.
    assert -30 < lufs < 0


def test_halving_amplitude_reads_6_db_quieter(tmp_path):
    loud = measure_loudness(_tone(tmp_path / "loud.wav", amplitude=0.5))
    quiet = measure_loudness(_tone(tmp_path / "quiet.wav", amplitude=0.25))
    assert loud is not None and quiet is not None
    # Exactly 6.02 dB of amplitude difference; allow a little gating slack.
    assert loud - quiet == pytest.approx(6.02, abs=0.15)


def test_silence_is_not_a_measurement(tmp_path):
    # BS.1770 reports digital silence as -inf, which must not reach the DB.
    assert measure_loudness(_tone(tmp_path / "mute.wav", amplitude=0.0)) is None


def test_too_short_to_gate(tmp_path):
    # Shorter than one 400 ms gating block.
    assert measure_loudness(_tone(tmp_path / "blip.wav", seconds=0.2)) is None


def test_unreadable_file_degrades_to_none(tmp_path):
    junk = tmp_path / "notaudio.mp3"
    junk.write_bytes(b"this is not audio")
    assert measure_loudness(str(junk)) is None
    assert read_loudness_tag(str(junk)) is None
    assert analyze_loudness(str(junk)) == (None, None)


def test_mono_file_measures(tmp_path):
    """librosa hands back a 1-D array for mono — the shape branch must cope."""
    path = tmp_path / "mono.wav"
    t = np.linspace(0, 3.0, int(SR * 3.0), endpoint=False)
    sf.write(str(path), 0.4 * np.sin(2 * np.pi * 440 * t), SR)
    assert measure_loudness(str(path)) is not None


# ── ReplayGain tag reading ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("-7.50 dB", -7.5),
    ("-7.50dB", -7.5),
    ("+2.3 dB", 2.3),
    ("-7.5", -7.5),
    (["-3.25 dB"], -3.25),
    (b"-4.00 dB", -4.0),
    ("", None),
    ("loud", None),
    (None, None),
])
def test_parse_gain_db(raw, expected):
    assert _parse_gain_db(raw) == expected


def test_reads_vorbis_replaygain_tag(tmp_path):
    from mutagen.flac import FLAC
    path = _tone(tmp_path / "tagged.flac", ext="flac")
    audio = FLAC(path)
    audio["REPLAYGAIN_TRACK_GAIN"] = "-5.00 dB"
    audio.save()
    # Stored gain is relative to the RG2 reference, so LUFS = ref + gain.
    assert read_loudness_tag(path) == pytest.approx(REPLAYGAIN_REF_LUFS - 5.0)


def test_reads_opus_style_r128_tag(tmp_path):
    """R128_TRACK_GAIN is Q7.8 fixed point against the -23 LUFS reference."""
    from mutagen.flac import FLAC
    path = _tone(tmp_path / "r128.flac", ext="flac")
    audio = FLAC(path)
    audio["R128_TRACK_GAIN"] = str(256 * 3)      # +3 dB
    audio.save()
    assert read_loudness_tag(path) == pytest.approx(-20.0)


def test_untagged_file_has_no_tag_loudness(tmp_path):
    assert read_loudness_tag(_tone(tmp_path / "plain.flac", ext="flac")) is None


def test_analyze_prefers_an_existing_tag_over_measuring(tmp_path):
    from mutagen.flac import FLAC
    path = _tone(tmp_path / "both.flac", ext="flac")
    audio = FLAC(path)
    audio["REPLAYGAIN_TRACK_GAIN"] = "-9.00 dB"
    audio.save()

    lufs, source = analyze_loudness(path)
    assert source == "tag" and lufs == pytest.approx(-27.0)

    # prefer_tag=False ignores it and measures the audio, which for this tone is
    # nowhere near the (fabricated) tag value.
    measured, source = analyze_loudness(path, prefer_tag=False)
    assert source == "measured" and measured != pytest.approx(-27.0)


def test_analyze_falls_back_to_measuring(tmp_path):
    lufs, source = analyze_loudness(_tone(tmp_path / "plain.wav"))
    assert source == "measured" and lufs is not None


# ── playback gain ────────────────────────────────────────────────────────────

def test_gain_attenuates_loud_tracks():
    # 6 LU louder than target → turned down by 6 dB.
    assert gain_db_for(-8.0, -14.0) == pytest.approx(-6.0)


def test_gain_never_boosts_quiet_tracks():
    """The player scales HTMLMediaElement.volume, which can't exceed 1.0."""
    assert gain_db_for(-20.0, -14.0) == 0.0


def test_gain_is_neutral_at_target():
    assert gain_db_for(-14.0, -14.0) == 0.0


def test_unmeasured_track_plays_untouched():
    assert gain_db_for(None, -14.0) == 0.0


# ── storage ──────────────────────────────────────────────────────────────────

def _upsert(db, path, **kw):
    db.upsert_track(path, "1:1", 128.0, None, None, None, 0.9, "librosa", "done", **kw)


def test_upsert_stores_loudness(db):
    _upsert(db, "/music/a.mp3", loudness_lufs=-9.5, loudness_source="measured")
    row = db.get_track("/music/a.mp3")
    assert row["loudness_lufs"] == -9.5 and row["loudness_source"] == "measured"


def test_rescan_without_measurement_keeps_the_old_value(db):
    """A scan with measurement off (or an error pass) must not wipe a good value."""
    _upsert(db, "/music/a.mp3", loudness_lufs=-9.5, loudness_source="measured")
    _upsert(db, "/music/a.mp3")                     # no loudness passed
    row = db.get_track("/music/a.mp3")
    assert row["loudness_lufs"] == -9.5 and row["loudness_source"] == "measured"


def test_error_pass_keeps_the_old_value(db):
    _upsert(db, "/music/a.mp3", loudness_lufs=-9.5, loudness_source="measured")
    db.upsert_track("/music/a.mp3", "1:2", None, None, None, None, None, None,
                    "error", error="boom")
    assert db.get_track("/music/a.mp3")["loudness_lufs"] == -9.5


def test_save_loudness_overwrites_a_tag_estimate(db):
    """The re-measure action must be able to replace a 'tag'-sourced value."""
    _upsert(db, "/music/a.mp3", loudness_lufs=-23.0, loudness_source="tag")
    db.save_loudness("/music/a.mp3", -11.25, "measured")
    row = db.get_track("/music/a.mp3")
    assert row["loudness_lufs"] == -11.25 and row["loudness_source"] == "measured"


def test_unmeasured_tracking(db):
    _upsert(db, "/music/measured.mp3", loudness_lufs=-9.5, loudness_source="measured")
    _upsert(db, "/music/pending.mp3")
    db.upsert_track("/music/gone.mp3", "1:1", None, None, None, None, None, None,
                    "deleted")

    assert db.count_unmeasured_loudness() == 1          # deleted rows excluded
    assert db.get_unmeasured_loudness_paths(10) == ["/music/pending.mp3"]

    db.save_loudness("/music/pending.mp3", -12.0, "measured")
    assert db.count_unmeasured_loudness() == 0
    assert db.get_unmeasured_loudness_paths(10) == []


def test_run_candidates_carry_loudness(db):
    """The run queue is built from this pool, so the value has to survive it."""
    _upsert(db, "/music/a.mp3", loudness_lufs=-8.0, loudness_source="measured")
    rows = db.get_run_candidates()
    assert rows and rows[0]["loudness_lufs"] == -8.0


def test_play_all_paths_carry_loudness(db):
    _upsert(db, "/music/a.mp3", loudness_lufs=-8.0, loudness_source="measured")
    rows = db.get_track_paths()
    assert rows and rows[0]["loudness_lufs"] == -8.0


# ── web API ──────────────────────────────────────────────────────────────────

def _app(base_config, **over):
    import os

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


def test_me_reports_the_playback_normalization_settings(base_config):
    """The player reads these on boot to decide how far to attenuate."""
    client = _app(base_config, normalize_playback=True,
                  loudness_target_lufs=-16.0).test_client()
    _login(client)
    me = client.get("/api/me").get_json()
    assert me["normalize_playback"] is True
    assert me["loudness_target_lufs"] == -16.0


def test_fill_status_reports_what_is_left(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        _upsert(state().db, "/music/pending.mp3")
    body = client.get("/api/loudness/fill/status").get_json()
    assert body["running"] is False and body["remaining"] == 1


def test_measure_endpoint_rejects_unknown_track(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    import os
    path = os.path.join(base_config["music_dir"], "nope.mp3")
    r = client.post("/api/track/loudness/measure",
                    json={"file_path": path}, headers=csrf)
    assert r.status_code == 404


def test_measure_endpoint_rejects_paths_outside_music_dir(base_config):
    client = _app(base_config).test_client()
    csrf = _login(client)
    r = client.post("/api/track/loudness/measure",
                    json={"file_path": "/etc/passwd"}, headers=csrf)
    assert r.status_code in (400, 403, 404)


def test_measure_endpoint_stores_a_fresh_measurement(base_config, tmp_path):
    import os
    import sqlite3
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    path = os.path.join(base_config["music_dir"], "tone.wav")
    _tone(path)
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("INSERT INTO tracks (file_path, title, status) VALUES (?, 'Tone', 'done')",
                 (path,))
    conn.commit()
    conn.close()

    r = client.post("/api/track/loudness/measure", json={"file_path": path}, headers=csrf)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body["loudness_source"] == "measured"
    assert -30 < body["loudness_lufs"] < 0
    from bpm_tagger.web.state import state
    with app.app_context():
        assert state().db.get_track(path)["loudness_lufs"] == body["loudness_lufs"]


def test_loudness_routes_are_closed_to_the_player_role(base_config):
    """Default-deny allowlist: back-fill and re-measure are admin-only."""
    client = _app(base_config, run_password="runpw").test_client()
    csrf = _login(client, "runpw")
    assert client.post("/api/loudness/fill/start", json={}, headers=csrf).status_code == 403
    assert client.post("/api/track/loudness/measure", json={"file_path": "/x"},
                       headers=csrf).status_code == 403
