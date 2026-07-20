"""Brute-force login throttling: per-IP, per-account (distributed), and global.

The per-IP layer is the original one; the per-account and global layers were
added so a distributed attack (many source IPs) can't slip under the per-IP cap.
"""


def _attempt(app, ip, **body):
    """One login attempt from a chosen source IP (so tests can spread attempts
    across hosts the way a distributed attacker would)."""
    return app.test_client().post(
        "/api/login", json=body, environ_overrides={"REMOTE_ADDR": ip})


def _admin(app):
    c = app.test_client()
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}


# ── admin reset-lockouts action (clears state, not policy) ─────────────────────
def test_reset_lockouts_clears_all_layers(client, app):
    ip = "10.5.5.5"
    for _ in range(5):
        assert _attempt(app, ip, password="wrong").status_code == 401
    assert _attempt(app, ip, password="wrong").status_code == 429   # locked
    # Admin clears the lockouts …
    c, csrf = _admin(app)
    assert c.post("/api/settings/reset-lockouts", headers=csrf).status_code == 200
    # … and the previously-locked IP can log in again immediately.
    assert _attempt(app, ip, password="s3cret").status_code == 200


def test_reset_lockouts_is_admin_only(client, app):
    c, csrf = _admin(app)
    c.post("/api/players", json={"username": "runner", "password": "runrunrun"}, headers=csrf)
    pc = app.test_client()
    pc.post("/api/login", json={"username": "runner", "password": "runrunrun"})
    pcsrf = {"X-CSRF-Token": pc.get("/api/me").get_json()["csrf_token"]}
    assert pc.post("/api/settings/reset-lockouts", headers=pcsrf).status_code == 403


def _make_player(app, username="runner", password="runrunrun"):
    c, csrf = _admin(app)
    assert c.post("/api/players", json={"username": username, "password": password},
                  headers=csrf).status_code == 200


# ── per-IP (original behaviour, preserved through the refactor) ────────────────
def test_per_ip_lockout(client, app):
    ip = "10.0.0.1"
    for _ in range(5):
        assert _attempt(app, ip, password="wrong").status_code == 401
    # 6th attempt is refused before the password is checked.
    r = _attempt(app, ip, password="wrong")
    assert r.status_code == 429 and r.get_json()["error"] == "locked_out"
    # Even the correct admin password is refused from the locked IP …
    assert _attempt(app, ip, password="s3cret").status_code == 429
    # … but a different IP is unaffected.
    assert _attempt(app, "10.0.0.2", password="s3cret").status_code == 200


# ── per-account: catches a distributed attack the per-IP layer can't see ───────
def test_per_account_lockout_across_ips(client, app):
    _make_player(app)
    # Drop the account cap so the test needn't fire 15 requests; the point is that
    # the cap is enforced across DIFFERENT IPs (per-IP alone would never trip).
    app.extensions["state"].account_max_login_attempts = 5
    for i in range(5):
        assert _attempt(app, f"172.16.0.{i}", username="runner",
                        password="wrong").status_code == 401
    # The account is now locked regardless of source IP …
    assert _attempt(app, "172.16.9.9", username="runner",
                    password="wrong").status_code == 429
    # … including for the CORRECT password.
    assert _attempt(app, "172.16.9.10", username="runner",
                    password="runrunrun").status_code == 429
    # A different account from a fresh IP is unaffected (blank username = admin).
    assert _attempt(app, "172.16.9.11", password="s3cret").status_code == 200


def test_per_account_cap_is_higher_than_per_ip(client, app):
    """The shared admin/guest key tolerates more than the per-IP cap, so a few
    hostile requests can't lock the single admin out from every IP."""
    # Six blank-username failures across six IPs: per-IP never trips (1 each) and
    # the per-account default (15) is well clear, so a fresh IP still logs in.
    for i in range(6):
        assert _attempt(app, f"198.51.100.{i}", password="wrong").status_code == 401
    assert _attempt(app, "198.51.100.99", password="s3cret").status_code == 200


# ── global backstop: a broad sweep (many IPs, many accounts) ───────────────────
def test_global_lockout_backstop(client, app):
    app.extensions["state"].global_max_login_attempts = 3
    # Three failures, each a distinct IP AND username, so neither the per-IP nor
    # the per-account layer trips first — only the global counter accrues.
    for i in range(3):
        assert _attempt(app, f"192.0.2.{i}", username=f"u{i}",
                        password="x").status_code == 401
    # The next attempt from a fresh IP/account is refused by the global backstop.
    r = _attempt(app, "192.0.2.9", username="u9", password="x")
    assert r.status_code == 429 and r.get_json()["error"] == "locked_out"
    # A valid admin login is blocked too while the global cooldown holds.
    assert _attempt(app, "192.0.2.10", password="s3cret").status_code == 429


# ── a success resets the per-IP and per-account counters ──────────────────────
def test_success_resets_counters(client, app):
    _make_player(app)
    ip = "203.0.113.5"
    for _ in range(4):
        assert _attempt(app, ip, username="runner", password="wrong").status_code == 401
    # 4 < 5 → the correct password still works and clears the counters.
    assert _attempt(app, ip, username="runner", password="runrunrun").status_code == 200
    # Proof of reset: it now takes a full five failures again to lock (without the
    # reset the 2nd of these would already be a 429).
    for _ in range(5):
        assert _attempt(app, ip, username="runner", password="wrong").status_code == 401
    assert _attempt(app, ip, username="runner", password="wrong").status_code == 429
