"""Characterization tests for CSRF protection and login brute-force lockout."""


def _set_csrf(client, token="csrf-tok"):
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


def test_get_login_returns_form(client):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_logout_without_csrf_is_forbidden(client):
    _set_csrf(client)
    resp = client.post("/logout")  # no token supplied
    assert resp.status_code == 403


def test_logout_with_csrf_header_redirects(client):
    token = _set_csrf(client)
    resp = client.post("/logout", headers={"X-CSRF-Token": token})
    assert resp.status_code == 302


def test_logout_with_csrf_form_field_redirects(client):
    token = _set_csrf(client)
    resp = client.post("/logout", data={"csrf_token": token})
    assert resp.status_code == 302


def test_login_lockout_after_max_attempts(client):
    token = _set_csrf(client)
    # 5 wrong attempts are allowed through (re-render form), the 6th trips lockout.
    for _ in range(5):
        resp = client.post("/login", data={"csrf_token": token, "password": "wrong"})
        assert resp.status_code != 429
    resp = client.post("/login", data={"csrf_token": token, "password": "wrong"})
    assert resp.status_code == 429


def test_login_success_with_correct_password(client):
    token = _set_csrf(client)
    resp = client.post("/login", data={"csrf_token": token, "password": "s3cret"})
    assert resp.status_code == 302
