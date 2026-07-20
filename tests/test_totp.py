"""Admin two-factor (TOTP): the stdlib code helpers, the login challenge flow,
and the enable/disable settings endpoints."""

from bpm_tagger.web import totp


def _admin(app):
    c = app.test_client()
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}


# ── the TOTP primitive ────────────────────────────────────────────────────────
def test_totp_verify_roundtrip_and_drift():
    secret = totp.generate_secret()
    now = 1_000_000.0
    code = totp._hotp(secret, int(now // totp.PERIOD))
    assert totp.verify(secret, code, now=now)
    # ±1 step tolerated, ±2 not.
    assert totp.verify(secret, code, now=now + totp.PERIOD)
    assert totp.verify(secret, code, now=now - totp.PERIOD)
    assert not totp.verify(secret, code, now=now + 2 * totp.PERIOD)


def test_totp_verify_rejects_junk():
    secret = totp.generate_secret()
    assert not totp.verify(secret, "")
    assert not totp.verify(secret, "12345")     # too short
    assert not totp.verify(secret, "abcdef")    # non-numeric
    assert not totp.verify("", "123456")        # no secret


def test_provisioning_uri_and_recovery_shape():
    secret = totp.generate_secret()
    uri = totp.provisioning_uri(secret, account="admin")
    assert uri.startswith("otpauth://totp/") and secret in uri and "issuer=BPM" in uri
    codes = totp.generate_recovery_codes(8)
    assert len(codes) == 8 and all("-" in c for c in codes)


# ── login challenge flow ──────────────────────────────────────────────────────
def _enable_2fa(app):
    """Turn on 2FA directly in live config (bypassing the setup UI) for login tests."""
    secret = totp.generate_secret()
    st = app.extensions["state"]
    st.config["totp_enabled"] = True
    st.config["totp_secret"] = secret
    st.config["totp_recovery_hashes"] = []
    return secret


def test_login_requires_code_when_2fa_on(client, app):
    secret = _enable_2fa(app)
    c = app.test_client()
    # Right password, no code → challenge (not a plain rejection).
    r = c.post("/api/login", json={"password": "s3cret"})
    assert r.status_code == 401 and r.get_json()["error"] == "totp_required"
    # Wrong code → rejected as a code problem.
    r = c.post("/api/login", json={"password": "s3cret", "totp": "000000"})
    assert r.status_code == 401 and r.get_json()["error"] == "totp_invalid"
    # Correct current code → in.
    code = totp._hotp(secret, int(__import__("time").time() // totp.PERIOD))
    r = c.post("/api/login", json={"password": "s3cret", "totp": code})
    assert r.status_code == 200 and r.get_json()["role"] == "admin"


def test_wrong_password_never_reveals_2fa(client, app):
    _enable_2fa(app)
    # A wrong password is a plain invalid_password — the 2FA stage is never reached.
    r = app.test_client().post("/api/login", json={"password": "nope"})
    assert r.status_code == 401 and r.get_json()["error"] == "invalid_password"


def test_recovery_code_logs_in_once(client, app):
    from werkzeug.security import generate_password_hash
    _enable_2fa(app)
    st = app.extensions["state"]
    st.config["totp_recovery_hashes"] = [generate_password_hash(totp.normalize_recovery_code("abcde-fghij"))]
    c = app.test_client()
    # First use works …
    r = c.post("/api/login", json={"password": "s3cret", "totp": "ABCDE-FGHIJ"})
    assert r.status_code == 200
    # … and is consumed: the same code no longer works.
    r = app.test_client().post("/api/login", json={"password": "s3cret", "totp": "abcde-fghij"})
    assert r.status_code == 401 and r.get_json()["error"] == "totp_invalid"


# ── enable / disable endpoints ────────────────────────────────────────────────
def test_enable_disable_flow(client, app):
    c, csrf = _admin(app)
    setup = c.post("/api/settings/totp/setup", headers=csrf).get_json()
    assert setup["ok"] and setup["secret"] and setup["otpauth_uri"].startswith("otpauth://")
    # Wrong confirm code is refused.
    assert c.post("/api/settings/totp/confirm", json={"code": "000000"}, headers=csrf).status_code == 400
    # Correct code enables and returns recovery codes.
    code = totp._hotp(setup["secret"], int(__import__("time").time() // totp.PERIOD))
    conf = c.post("/api/settings/totp/confirm", json={"code": code}, headers=csrf)
    assert conf.status_code == 200 and len(conf.get_json()["recovery_codes"]) == 10
    assert app.extensions["state"].config["totp_enabled"] is True
    # GET reports it enabled with the recovery count.
    got = c.get("/api/settings", headers=csrf).get_json()["settings"]
    assert got["totp_enabled"] is True and got["totp_recovery_remaining"] == 10
    assert got["totp_secret"] == "********"           # secret never leaks
    # Disable requires the current password.
    assert c.post("/api/settings/totp/disable", json={"password": "wrong"}, headers=csrf).status_code == 400
    assert c.post("/api/settings/totp/disable", json={"password": "s3cret"}, headers=csrf).status_code == 200
    assert app.extensions["state"].config["totp_enabled"] is False


def test_totp_endpoints_are_admin_only(client, app):
    # Create a player, log in as them, and confirm they can't touch 2FA.
    c, csrf = _admin(app)
    c.post("/api/players", json={"username": "runner", "password": "runrunrun"}, headers=csrf)
    pc = app.test_client()
    pc.post("/api/login", json={"username": "runner", "password": "runrunrun"})
    pcsrf = {"X-CSRF-Token": pc.get("/api/me").get_json()["csrf_token"]}
    assert pc.post("/api/settings/totp/setup", headers=pcsrf).status_code == 403
